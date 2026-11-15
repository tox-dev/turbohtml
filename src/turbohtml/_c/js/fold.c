/* Peephole folding over the arena AST, run before mangling on the full-minify path.

   Each fold rewrites a node in place into an equivalent shorter form as a real AST
   node, so the printer's precedence logic re-parenthesizes it correctly (e.g.
   `true.x` becomes `(!0).x`, never the wrong `!0.x`). The pass is post-order: children
   fold first, so a parent sees already-folded constant operands.

   Literal folds (always safe; reserved words are never bindings, and the global
   `undefined` check is whole-program):
     true / false -> !0 / !1
     undefined    -> void 0        (only when nothing shadows undefined)

   Constant-driven dead-code elimination, the constant-folding pass every optimizing JS minifier runs. A
   transform only drops an operand when it is a *pure* constant (no side effects) or
   the operand is on the never-taken side of a short circuit; and it only drops a
   *statement* subtree when that subtree declares no hoisted `var` or function (which
   would otherwise survive the branch) -- a conservative check that skips the fold
   rather than risk the classic hoisting miscompile:
     !<pure const>            -> the boolean
     <pure const> && b        -> b or the const
     <pure const> || b        -> the const or b
     <null|undefined> ?? b    -> b ;  <other pure const> ?? b -> the const
     <pure const> ? a : b     -> a or b
     if (<pure const>) a else b   -> the taken branch (if the dropped branch hoists nothing)
     ...; return x; <dead>        -> drop <dead> (when it hoists nothing)

   Validated by execution differentials under Node. */

#include "core/work_steps.h"
#include "js/internal.h"

#include <string.h>

typedef struct {
    jm_program *prog;
    int changed;     /* a transform fired this pass: jm_fold walks again so cascades finish */
    int with_depth;  /* enclosing with bodies: their object may own an `undefined` property */
    int32_t deepest; /* the largest depth reached under the if, logical or statement list being folded */
    int32_t pass;    /* the running pass, counted from 1: the stamp node_hoists leaves in hoists */
    int32_t *hoists; /* hoists[node] == pass: node_hoists found a hoisted declaration under node in this pass */
    int32_t hoists_cap;
} F;

static int ident_is(const jm_node *node, const char *word) {
    Py_ssize_t len = 0;
    while (word[len] != '\0') {
        len++;
    }
    if (node->str_len != len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (node->str[index] != (Py_UCS4)(unsigned char)word[index]) {
            return 0;
        }
    }
    return 1;
}

/* Overwrite the node at dst with the node at src, keeping dst's sibling link so a
   replacement inside a statement or argument chain stays connected. */
static void replace_with(F *folder, int32_t dst, int32_t src) {
    jm_node_replace(folder->prog, dst, src);
    folder->changed = 1;
}

/* Whether a number lexeme is the value zero: 1 zero, 0 non-zero, -1 unknown (an
   exponent or BigInt suffix is left to the engine rather than guessed). */
static int number_is_zero(const jm_node *node) {
    Py_ssize_t start = 0;
    if (node->str_len >= 2 && node->str[0] == '0' &&
        (node->str[1] == 'x' || node->str[1] == 'X' || node->str[1] == 'o' || node->str[1] == 'O' ||
         node->str[1] == 'b' || node->str[1] == 'B')) {
        start = 2;
    }
    int zero = 1;
    for (Py_ssize_t index = start; index < node->str_len; index++) {
        Py_UCS4 ch = node->str[index];
        if (ch == 'e' || ch == 'E') { /* an exponent: leave the value to the engine */
            return -1;
        }
        if (ch != '0' && ch != '.' && ch != '_') {
            zero = 0;
        }
    }
    return zero;
}

/* Whether a string literal's value is the empty string. A non-empty lexeme still has an empty value
   when its only content is LineContinuations (ECMA-262 §12.9.4.1: `\`+LineTerminatorSequence yields
   nothing), so truthiness must look through the escapes, not measure the lexeme (#436). */
static int string_value_empty(const jm_node *node) {
    const Py_UCS4 *inner = node->str + 1;
    Py_ssize_t len = node->str_len - 2;
    for (Py_ssize_t index = 0; index < len;) {
        if (inner[index] != '\\') {
            return 0; /* a literal code point makes the value non-empty */
        }
        if (index + 1 >= len) { /* GCOVR_EXCL_BR_LINE: a lone trailing backslash is malformed input only */
            return 0;           /* GCOVR_EXCL_LINE */
        }
        Py_UCS4 next = inner[index + 1];
        if (next == '\r' && index + 2 < len && inner[index + 2] == '\n') { /* a CRLF continuation */
            index += 3;
        } else if (next == '\n' || next == '\r' || next == 0x2028 || next == 0x2029) {
            index += 2;
        } else {
            return 0; /* any other escape yields at least one code point */
        }
    }
    return 1;
}

/* The truthiness of a *pure* (side-effect-free) constant: 1 truthy, 0 falsy, -1 not a
   pure constant (so neither its value is known nor may it be dropped). */
static int pure_truthy(F *folder, int32_t idx) {
    TH_WORK_STEP();
    jm_node *node = &folder->prog->nodes[idx];
    switch (node->kind) {
    case JN_NUM: {
        int zero = number_is_zero(node);
        return zero < 0 ? -1 : zero ? 0 : 1;
    }
    case JN_STRING:
        return string_value_empty(node) ? 0 : 1;
    case JN_REGEX:
        return 1;
    case JN_IDENT:
        return ident_is(node, "null") ? 0 : -1;
    case JN_UNARY:
        if (node->op == JT_NOT) {
            /* walk() folds bottom-up, so a `!` still over a unary is not a constant. Descending the chain from
               each of its levels made every pass quadratic in its length. A rewrite may stack a `!` over a constant
               mid-pass; reading it as unknown is safe, and the next pass folds it. */
            if (folder->prog->nodes[node->a].kind == JN_UNARY) {
                return -1;
            }
            int inner = pure_truthy(folder, node->a);
            return inner < 0 ? -1 : !inner;
        }
        if (node->op == JT_IDENT && ident_is(node, "void")) {  /* op==JT_IDENT implies str set */
            return pure_truthy(folder, node->a) >= 0 ? 0 : -1; /* void <pure> is undefined */
        }
        return -1;
    default:
        return -1;
    }
}

static int is_pure_const(F *folder, int32_t idx) {
    return pure_truthy(folder, idx) >= 0;
}

/* The primitive type an expression is statically certain to evaluate to -- 's'tring, 'n'umber,
   'b'oolean, 'u'ndefined -- or 0 when unknown. Only shapes whose type the grammar fixes qualify:
   a literal, `typeof` (always a string, 13.5.3), `!` (always a boolean, 13.5.7), `void` (always
   undefined, 13.5.2). */
static char static_type(F *folder, int32_t idx) {
    const jm_node *node = &folder->prog->nodes[idx];
    switch (node->kind) {
    case JN_STRING:
        return 's';
    case JN_NUM:
        return 'n';
    case JN_UNARY:
        if (node->op == JT_NOT) {
            return 'b';
        }
        if (node->op == JT_IDENT && ident_is(node, "typeof")) {
            return 's';
        }
        if (node->op == JT_IDENT && ident_is(node, "void")) {
            return 'u';
        }
        return 0;
    default:
        return 0;
    }
}

/* Whether the string literal spells `word` (the lexeme keeps its quotes; an escape in the
   lexeme fails the character match, so the decoded value always agrees). */
static int string_is(const jm_node *node, const char *word) {
    if (node->kind != JN_STRING) {
        return 0;
    }
    Py_ssize_t len = 0;
    while (word[len] != '\0') {
        len++;
    }
    if (node->str_len != len + 2) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (node->str[index + 1] != (Py_UCS4)(unsigned char)word[index]) {
            return 0;
        }
    }
    return 1;
}

/* A `typeof X` whose X is not a bare name. typeof suppresses the ReferenceError of an
   unresolvable name (13.5.3 step 2), so only a non-name operand evaluates identically
   outside the typeof. */
static int is_typeof_nonref(F *folder, int32_t idx) {
    const jm_node *node = &folder->prog->nodes[idx];
    return node->kind == JN_UNARY && node->op == JT_IDENT && ident_is(node, "typeof") &&
           folder->prog->nodes[node->a].kind != JN_IDENT;
}

/* Whether a pure constant is null or undefined (the operands `??` short-circuits on). */
static int is_nullish(F *folder, int32_t idx) {
    jm_node *node = &folder->prog->nodes[idx];
    if (node->kind == JN_IDENT) {
        return ident_is(node, "null");
    }
    if (node->kind == JN_UNARY && node->op == JT_IDENT && ident_is(node, "void")) {
        return is_pure_const(folder, node->a); /* op==JT_IDENT implies str set */
    }
    return 0;
}

static int chain_hoists(F *folder, int32_t first);
static int scan_hoists(F *folder, int32_t idx);

/* Whether the statement subtree at idx declares a `var` or a function that hoists out
   of a dropped branch. Descends through control flow but never into nested function
   scopes (their declarations do not hoist here). Over-approximating is safe; this
   never under-reports, so a dropped branch can never silently lose a hoisted name.

   A kept branch makes every statement around it ask about a subtree that already holds the declaration, so a
   found declaration is remembered until the pass ends and the scan stops at the first remembered node. Only
   positive answers are remembered: a fold may still move a declaration into a subtree that said no. */
static int node_hoists(F *folder, int32_t idx) {
    if (idx < 0) {
        return 0;
    }
    TH_WORK_STEP();
    if (idx < folder->hoists_cap && folder->hoists[idx] == folder->pass) {
        return 1;
    }
    if (!scan_hoists(folder, idx)) {
        return 0;
    }
    if (idx >= folder->hoists_cap) {
        int32_t cap = folder->prog->node_cap;
        int32_t *grown = jm_realloc(folder->hoists, (size_t)cap * sizeof(int32_t));
        if (grown == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
            folder->prog->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            return 1;                 /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        memset(grown + folder->hoists_cap, 0, (size_t)(cap - folder->hoists_cap) * sizeof(int32_t));
        folder->hoists = grown;
        folder->hoists_cap = cap;
    }
    folder->hoists[idx] = folder->pass;
    return 1;
}

/* The scan behind node_hoists. It recurses through node_hoists, so every statement on the way down to the
   declaration is remembered too. */
static int scan_hoists(F *folder, int32_t idx) {
    jm_node *node = &folder->prog->nodes[idx];
    switch (node->kind) {
    case JN_VAR:
        return node->decl == 0; /* var hoists; let / const are block scoped */
    case JN_FUNC:
        return !(node->flags & JN_F_EXPR); /* a function declaration hoists */
    case JN_BLOCK:
        return chain_hoists(folder, node->a);
    case JN_IF:
        return node_hoists(folder, node->b) || node_hoists(folder, node->c);
    case JN_FOR:
        return node_hoists(folder, node->a) || node_hoists(folder, node->d);
    case JN_FORIN:
    case JN_FOROF:
        return node_hoists(folder, node->a) || node_hoists(folder, node->c);
    case JN_WHILE:
    case JN_WITH:
        return node_hoists(folder, node->b);
    case JN_DOWHILE:
    case JN_LABEL:
        return node_hoists(folder, node->a);
    case JN_TRY:
        return node_hoists(folder, node->a) || node_hoists(folder, node->c) || node_hoists(folder, node->d);
    case JN_SWITCH:
        for (int32_t clause = node->b; clause >= 0; clause = folder->prog->nodes[clause].next) {
            if (chain_hoists(folder, folder->prog->nodes[clause].b)) {
                return 1;
            }
        }
        return 0;
    default:
        return 0; /* expressions and arrows declare nothing that hoists here */
    }
}

static int chain_hoists(F *folder, int32_t first) {
    for (int32_t idx = first; idx >= 0; idx = folder->prog->nodes[idx].next) {
        if (node_hoists(folder, idx)) {
            return 1;
        }
    }
    return 0;
}

static void fold_boolean(F *folder, int32_t idx, int truth) {
    static const Py_UCS4 zero = '0';
    static const Py_UCS4 one = '1';
    int32_t num = jm_node_new(folder->prog, JN_NUM);
    if (num < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path leaves the literal as-is */
        return;    /* GCOVR_EXCL_LINE */
    }
    folder->prog->nodes[num].str = truth ? &zero : &one;
    folder->prog->nodes[num].str_len = 1;
    jm_node *node = &folder->prog->nodes[idx];
    node->kind = JN_UNARY;
    node->op = JT_NOT;
    node->str = NULL;
    node->str_len = 0;
    node->a = num;
    folder->changed = 1;
}

static void fold_void(F *folder, int32_t idx) {
    static const Py_UCS4 zero = '0';
    static const Py_UCS4 voidkw[] = {'v', 'o', 'i', 'd'};
    int32_t num = jm_node_new(folder->prog, JN_NUM);
    if (num < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return;    /* GCOVR_EXCL_LINE */
    }
    folder->prog->nodes[num].str = &zero;
    folder->prog->nodes[num].str_len = 1;
    jm_node *node = &folder->prog->nodes[idx];
    node->kind = JN_UNARY;
    node->op = JT_IDENT; /* a word operator: the printer emits node->str then the operand */
    node->str = voidkw;
    node->str_len = 4;
    node->a = num;
    folder->changed = 1;
}

static int pattern_binds(F *folder, int32_t idx, const char *name) {
    if (idx < 0) {
        return 0;
    }
    jm_node *node = &folder->prog->nodes[idx];
    switch (node->kind) {
    case JN_IDENT:
        return ident_is(node, name);
    case JN_ARRAY:
        for (int32_t element = node->a; element >= 0; element = folder->prog->nodes[element].next) {
            if (pattern_binds(folder, element, name)) {
                return 1;
            }
        }
        return 0;
    case JN_OBJECT:
        for (int32_t prop = node->a; prop >= 0; prop = folder->prog->nodes[prop].next) {
            jm_node *pn = &folder->prog->nodes[prop];
            int32_t target = pn->kind == JN_SPREAD ? pn->a : pn->b >= 0 ? pn->b : pn->a;
            if (pattern_binds(folder, target, name)) {
                return 1;
            }
        }
        return 0;
    case JN_ASSIGN:
    case JN_SPREAD:
        return pattern_binds(folder, node->a, name);
    default:
        return 0;
    }
}

static int declares(F *folder, int32_t idx, const char *name) {
    while (idx >= 0) {
        jm_node *node = &folder->prog->nodes[idx];
        switch (node->kind) {
        case JN_VAR:
            for (int32_t declarator = node->a; declarator >= 0; declarator = folder->prog->nodes[declarator].next) {
                if (pattern_binds(folder, folder->prog->nodes[declarator].a, name) ||
                    declares(folder, folder->prog->nodes[declarator].b, name)) {
                    return 1;
                }
            }
            break;
        case JN_FUNC:
        case JN_ARROW:
            if (node->str != NULL && ident_is(node, name)) {
                return 1;
            }
            for (int32_t param = node->a; param >= 0; param = folder->prog->nodes[param].next) {
                if (pattern_binds(folder, param, name)) {
                    return 1;
                }
            }
            if (declares(folder, node->b, name)) {
                return 1;
            }
            break;
        case JN_CLASS:
            if ((node->str != NULL && ident_is(node, name)) || declares(folder, node->a, name) ||
                declares(folder, node->b, name)) {
                return 1;
            }
            break;
        case JN_TRY:
            if (pattern_binds(folder, node->b, name) || declares(folder, node->a, name) ||
                declares(folder, node->c, name) || declares(folder, node->d, name)) {
                return 1;
            }
            break;
        default:
            if (declares(folder, node->a, name) || declares(folder, node->b, name) || declares(folder, node->c, name) ||
                declares(folder, node->d, name)) {
                return 1;
            }
            break;
        }
        idx = node->next;
    }
    return 0;
}

static void walk(F *folder, int32_t idx, int32_t depth);

static void walk_block(F *folder, int32_t idx, int32_t depth, int member);

static void walk_if(F *folder, int32_t idx, int32_t depth, int member);

static inline void walk_stmt(F *folder, int32_t idx, int32_t depth, int member);

static void walk_chain(F *folder, int32_t first, int32_t depth) {
    for (int32_t idx = first; idx >= 0; idx = folder->prog->nodes[idx].next) {
        walk(folder, idx, depth);
    }
}

/* Walk the statements of a list that its owner folds with optimize_chain next. The members are walked as such,
   so that what one hands to the list -- a redundant block its statements, a constant `if` its taken
   branch, an `if` that always jumps its else -- is folded once, with the list, and not on its own first:
   a nest would fold its innermost statements again at every level. An else spliced in behind a member is
   reached through `next` and walked in turn. */
static void walk_members(F *folder, int32_t first, int32_t depth) {
    for (int32_t idx = first; idx >= 0; idx = folder->prog->nodes[idx].next) {
        walk_stmt(folder, idx, depth, 1);
    }
}

/* The first statement of a list that declares a name outliving the statements, or -1: a var or function
   hoists out of any nested statement, and a let, const or class at the list's own level covers the whole
   block from its start (14.2.3), so a read above it throws in its TDZ rather than reaching an outer
   binding. A class in a statement list is always a declaration. */
static int32_t first_declaration(F *folder, int32_t first) {
    for (int32_t idx = first; idx >= 0; idx = folder->prog->nodes[idx].next) {
        int kind = folder->prog->nodes[idx].kind;
        if (kind == JN_VAR || kind == JN_CLASS || node_hoists(folder, idx)) {
            return idx;
        }
    }
    return -1;
}

/* Drop the statements that follow a return / throw / break / continue, up to the first one that declares
   a name (a hoisted one survives the jump, a lexical one shadows its block). The scan goes on from that
   declaration, so a later jump cuts in turn. Stopping at a declaration, rather than keeping the whole
   tail when one follows anywhere, cuts the tail of a redundant block the same whether or not the list
   its statements are spliced into declares a name further down. */
static void drop_unreachable(F *folder, int32_t first) {
    for (int32_t idx = first; idx >= 0; idx = folder->prog->nodes[idx].next) {
        jm_node *node = &folder->prog->nodes[idx];
        if (node->kind != JN_RETURN && node->kind != JN_THROW && node->kind != JN_BREAK && node->kind != JN_CONTINUE) {
            continue;
        }
        int32_t kept = first_declaration(folder, node->next);
        if (kept != node->next) {
            node->next = kept; /* cut the unreachable statements before it */
            folder->changed = 1;
        }
    }
}

/* Merge each run of adjacent same-kind declarations into one: `var a=1;var b=2` becomes
   `var a=1,b=2`. Only touches a statement list (never a for-header), and only joins lists of
   the identical declaration kind, so scoping and hoisting are unchanged. No node is allocated,
   so the cached arena base stays valid. */
static int32_t merge_declarations(F *folder, int32_t first) {
    jm_node *nodes = folder->prog->nodes;
    int32_t prev = -1;
    for (int32_t idx = first; idx >= 0;) {
        int32_t next = nodes[idx].next;
        if (next >= 0 && nodes[idx].kind == JN_VAR && nodes[next].kind == JN_VAR &&
            nodes[idx].decl == nodes[next].decl) {
            int32_t tail = nodes[idx].a;
            do {
                while (nodes[tail].next >= 0) {
                    tail = nodes[tail].next;
                }
                nodes[tail].next = nodes[next].a;
                nodes[idx].next = nodes[next].next;
                next = nodes[idx].next;
            } while (next >= 0 && nodes[next].kind == JN_VAR && nodes[idx].decl == nodes[next].decl);
            folder->changed = 1;
            continue;
        }
        if (next >= 0 && nodes[idx].kind == JN_VAR && nodes[idx].decl == 0 && nodes[next].kind == JN_FOR &&
            (nodes[next].a < 0 || (nodes[nodes[next].a].kind == JN_VAR && nodes[nodes[next].a].decl == 0))) {
            /* `var A;for(...)` becomes the for's init: a var hoists to the function either way and
               its initializers run in the same order right before the first test (14.7.4), so an
               empty init takes the statement and a var init prepends its declarators. A let/const
               never moves -- the head would re-scope it away from the code after the loop. */
            int32_t init = nodes[next].a;
            if (init >= 0) {
                int32_t tail = nodes[idx].a;
                while (nodes[tail].next >= 0) {
                    tail = nodes[tail].next;
                }
                nodes[tail].next = nodes[init].a;
            }
            nodes[next].a = idx;
            nodes[idx].next = -1;
            if (prev < 0) {
                first = next;
            } else {
                nodes[prev].next = next;
            }
            folder->changed = 1;
            idx = next;
            continue;
        }
        prev = idx;
        idx = next;
    }
    return first;
}

/* The single statement of an `if` branch: the statement itself, or the lone statement of a
   one-statement block (a block with a single non-declaration statement adds no observable scope).
   -1 if the branch is empty or a multi-statement block. The caller only rewrites when the result
   is an expression statement or a return, so unwrapping a block whose statement is a declaration
   is harmless (it is rejected there). */
static int32_t branch_stmt(F *folder, int32_t idx) {
    if (folder->prog->nodes[idx].kind == JN_BLOCK) { /* callers pass a real consequent/alternate */
        int32_t inner = folder->prog->nodes[idx].a;
        return inner < 0 || folder->prog->nodes[inner].next >= 0 ? -1 : inner;
    }
    return idx;
}

/* Whether a branch is a no-op: a bare `;` or a block that holds no statement. optimize_chain's
   drop_empties has already stripped stray `;` from every block by the time this runs, so an empty
   block is exactly one with no children. */
static int is_empty_branch(F *folder, int32_t idx) {
    const jm_node *node = &folder->prog->nodes[idx];
    return node->kind == JN_EMPTY || (node->kind == JN_BLOCK && node->a < 0);
}

/* Left-rotate a same-operator `&&`/`||`/`??` chain. Short-circuit evaluation makes the operator
   associative -- `a&&(b&&c)` and `(a&&b)&&c` evaluate a, b, c in the same order with the same
   result (13.13) -- and the grammar is left-associative, so only the left-leaning shape prints
   without parentheses (`a&&b&&c`) and re-parses to itself. `settled` is a left operand already
   known to be left-leaning (-1: none); the descent stops there, because walk has folded it and
   descending the whole spine from every node of a chain is quadratic in the chain's length.
   Each rotation moves the left operand one level down and no other operand deeper, so sibling
   parenthesised groups, which the parser meters one at a time, would join into a spine as tall
   as their sum. left_deepest is the deepest point under the left operand (or a bound above it):
   once a rotation would push that past prog->rewrite_limit the rotation stops, and the printer
   parenthesises the right operand left in place. */
static void rotate_logical_chain(F *folder, int32_t idx, int32_t settled, int32_t left_deepest) {
    jm_program *prog = folder->prog;
    for (int32_t cur = idx;;) {
        TH_WORK_STEP();
        uint16_t op = prog->nodes[cur].op;
        while (prog->nodes[prog->nodes[cur].b].kind == JN_LOGICAL && prog->nodes[prog->nodes[cur].b].op == op) {
            if (left_deepest >= prog->rewrite_limit) {
                return;
            }
            if (++left_deepest > folder->deepest) {
                folder->deepest = left_deepest;
            }
            int32_t right = prog->nodes[cur].b;
            prog->nodes[cur].b = prog->nodes[right].b;
            prog->nodes[right].b = prog->nodes[right].a;
            prog->nodes[right].a = prog->nodes[cur].a;
            prog->nodes[cur].a = right;
            folder->changed = 1;
        }
        cur = prog->nodes[cur].a; /* a rotation may leave a same-op chain as the new left's right */
        if (cur == settled || prog->nodes[cur].kind != JN_LOGICAL || prog->nodes[cur].op != op) {
            return;
        }
    }
}

/* Whether the branch statement always completes abruptly with a jump: it is a return / throw /
   break / continue, or a block whose last statement is one. Control never falls out of such a
   consequent, so an `else` after it is redundant (14.6.2: the alternate runs exactly when the
   test was falsy either way). */
static int ends_abruptly(F *folder, int32_t idx) {
    const jm_node *node = &folder->prog->nodes[idx];
    if (node->kind == JN_BLOCK) { /* drop_empties has already stripped stray `;` from the block */
        int32_t last = node->a;
        if (last < 0) {
            return 0;
        }
        while (folder->prog->nodes[last].next >= 0) {
            last = folder->prog->nodes[last].next;
        }
        node = &folder->prog->nodes[last];
    }
    return node->kind == JN_RETURN || node->kind == JN_THROW || node->kind == JN_BREAK || node->kind == JN_CONTINUE;
}

/* The byte cost of negating an expression in a test position, with *wraps counting the fresh `!`
   nodes an application would need. Negation pays when the cost is negative: a `!x` sheds its bang
   (-1), an (in)equality flips its operator for free (7.2.13/7.2.14: != is the exact complement of
   ==), a short-circuit pair De-Morgans into the sum of its operands (13.13: !(a&&b) selects
   exactly like !a||!b in a boolean context), and anything else gains a bang (+1 when the operand
   binds at least as tight as the unary, else +3 for the parentheses). */
static int negate_cost(F *folder, int32_t idx, int *wraps) {
    const jm_node *node = &folder->prog->nodes[idx];
    if (node->kind == JN_UNARY && node->op == JT_NOT) {
        return -1;
    }
    if (node->kind == JN_BINARY &&
        (node->op == JT_EQ_EQ || node->op == JT_NE || node->op == JT_EQ_EQ_EQ || node->op == JT_NE_EQ)) {
        return 0;
    }
    if (node->kind == JN_LOGICAL && (node->op == JT_AND || node->op == JT_OR)) {
        return negate_cost(folder, node->a, wraps) + negate_cost(folder, node->b, wraps);
    }
    *wraps += 1;
    switch (node->kind) {
    case JN_IDENT:
    case JN_NUM:
    case JN_STRING:
    case JN_CALL:
    case JN_MEMBER_EXPR:
    case JN_UNARY:
        return 1;
    default:
        return 3;
    }
}

/* Apply the negation negate_cost priced, consuming the pre-allocated `!` nodes in pool -- no
   allocation happens here, so a half-applied tree is impossible. Returns the negated root. */
static int32_t negate_apply(F *folder, int32_t idx, const int32_t *pool, int *pool_next) {
    jm_node *node = &folder->prog->nodes[idx];
    if (node->kind == JN_UNARY && node->op == JT_NOT) {
        return node->a;
    }
    if (node->kind == JN_BINARY &&
        (node->op == JT_EQ_EQ || node->op == JT_NE || node->op == JT_EQ_EQ_EQ || node->op == JT_NE_EQ)) {
        node->op = node->op == JT_EQ_EQ      ? JT_NE
                   : node->op == JT_NE       ? JT_EQ_EQ
                   : node->op == JT_EQ_EQ_EQ ? JT_NE_EQ
                                             : JT_EQ_EQ_EQ;
        return idx;
    }
    if (node->kind == JN_LOGICAL && (node->op == JT_AND || node->op == JT_OR)) {
        node->op = node->op == JT_AND ? JT_OR : JT_AND;
        node->a = negate_apply(folder, node->a, pool, pool_next);
        node->b = negate_apply(folder, node->b, pool, pool_next);
        return idx;
    }
    int32_t wrap = pool[(*pool_next)++];
    folder->prog->nodes[wrap].op = JT_NOT;
    folder->prog->nodes[wrap].a = idx;
    return wrap;
}

/* Negate `*test` in place while that sheds bytes, returning how many negations applied (the
   caller flips its operator or swaps its branches once per application). Loops because peeling a
   doubled bang re-exposes another win. */
static int negate_test(F *folder, int32_t *test) {
    int applications = 0;
    for (;;) {
        int wraps = 0;
        if (negate_cost(folder, *test, &wraps) >= 0 || wraps > 16) {
            return applications;
        }
        int32_t pool[16];
        for (int slot = 0; slot < wraps; slot++) {
            pool[slot] = jm_node_new(folder->prog, JN_UNARY);
            if (pool[slot] < 0) {    /* GCOVR_EXCL_BR_LINE: allocation-failure bails before any mutation */
                return applications; /* GCOVR_EXCL_LINE */
            }
        }
        int consumed = 0;
        *test = negate_apply(folder, *test, pool, &consumed);
        applications++;
        folder->changed = 1;
    }
}

/* The boolean negation of an expression, for a test position (ToBoolean consumes the value, so
   only truthiness must invert): a `!x` peels to x, an (in)equality flips its operator in place
   (7.2.13/7.2.14: != is the exact complement of ==, !== of ===), and anything else gains a fresh
   `!`. Returns the negated node, or -1 on allocation failure. */
static int32_t negate_for_test(F *folder, int32_t idx) {
    jm_node *node = &folder->prog->nodes[idx];
    if (node->kind == JN_UNARY && node->op == JT_NOT) {
        return node->a;
    }
    if (node->kind == JN_BINARY &&
        (node->op == JT_EQ_EQ || node->op == JT_NE || node->op == JT_EQ_EQ_EQ || node->op == JT_NE_EQ)) {
        node->op = node->op == JT_EQ_EQ      ? JT_NE
                   : node->op == JT_NE       ? JT_EQ_EQ
                   : node->op == JT_EQ_EQ_EQ ? JT_NE_EQ
                                             : JT_EQ_EQ_EQ;
        return idx;
    }
    int32_t neg = jm_node_new(folder->prog, JN_UNARY);
    if (neg < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return -1; /* GCOVR_EXCL_LINE */
    }
    folder->prog->nodes[neg].op = JT_NOT;
    folder->prog->nodes[neg].a = idx;
    return neg;
}

/* Build `test ? then : els`, negating the test and swapping the branches when the negation sheds
   bytes (13.14: a conditional reads its test as a boolean, so `!x?a:b` selects like `x?b:a`). */
static int32_t make_cond(F *folder, int32_t test, int32_t then, int32_t els) {
    jm_program *prog = folder->prog;
    if (negate_test(folder, &test) % 2) {
        int32_t swap = then;
        then = els;
        els = swap;
    }
    int32_t cond = jm_node_new(prog, JN_COND);
    if (cond < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return -1;  /* GCOVR_EXCL_LINE */
    }
    prog->nodes[cond].a = test;
    prog->nodes[cond].b = then;
    prog->nodes[cond].c = els;
    return cond;
}

/* Build `test && expr` for a value-discarded position, negating the test and flipping the
   connective when that sheds bytes (13.13: as a statement, `!x&&e` and `x||e` run e at exactly
   the same time and their value is thrown away). */
static int32_t make_logical(F *folder, int32_t test, int32_t expr) {
    jm_program *prog = folder->prog;
    uint16_t op = negate_test(folder, &test) % 2 ? JT_OR : JT_AND;
    int32_t logic = jm_node_new(prog, JN_LOGICAL);
    if (logic < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return -1;   /* GCOVR_EXCL_LINE */
    }
    prog->nodes[logic].op = op;
    prog->nodes[logic].a = test;
    prog->nodes[logic].b = expr;
    /* expr may be a same-op chain (if(a)b&&c -> a&&b&&c), and negate_test can leave one on the test's
       spine (!a&&!(b||c) -> a||(b||c)), so the descent is not bounded */
    rotate_logical_chain(folder, logic, -1, folder->deepest);
    return logic;
}

/* Whether two nodes are the same identifier or literal -- the cases the conditional-algebra rewrites
   need to spot a repeated test or a shared assignment target. Two references to the same name in one
   expression denote the same binding (nothing can shadow between them), so a name match is enough;
   deeper shapes (member access, calls) are conservatively treated as different. */
static int same_expr(jm_program *prog, int32_t left, int32_t right) {
    const jm_node *first = &prog->nodes[left]; /* callers pass real subtrees (a conditional's test/branches) */
    const jm_node *second = &prog->nodes[right];
    if (first->kind != second->kind || first->str_len != second->str_len) {
        return 0;
    }
    switch (first->kind) {
    case JN_IDENT:
    case JN_NUM:
    case JN_STRING:
    case JN_REGEX:
    case JN_BIGINT:
        for (Py_ssize_t index = 0; index < first->str_len; index++) {
            if (first->str[index] != second->str[index]) {
                return 0;
            }
        }
        return 1;
    default:
        return 0;
    }
}

/* Overwrite the node at idx with a two-operand logical `left op right`. */
static void fold_to_logical(F *folder, int32_t idx, uint16_t op, int32_t left, int32_t right, int32_t depth) {
    int32_t logic = jm_node_new(folder->prog, JN_LOGICAL);
    if (logic < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return;      /* GCOVR_EXCL_LINE */
    }
    folder->prog->nodes[logic].op = op;
    folder->prog->nodes[logic].a = left;
    folder->prog->nodes[logic].b = right;
    /* right may be a same-op chain: x?x:y||z -> x||y||z; left is a name, one level under the node being folded */
    rotate_logical_chain(folder, logic, -1, depth + 1);
    replace_with(folder, idx, logic);
}

/* Conditional algebra (13.14): rewrite a `test ? cons : alt` whose branches share structure into a
   shorter equivalent. `test` is already known impure (a pure one folds to the taken branch earlier), so
   only rewrites that keep test's single evaluation and order are applied. */
static void fold_conditional(F *folder, int32_t idx, int32_t depth) {
    jm_program *prog = folder->prog;
    int32_t test = prog->nodes[idx].a;
    int32_t cons = prog->nodes[idx].b;
    int32_t alt = prog->nodes[idx].c;
    if (same_expr(prog, cons, alt)) {
        /* `x?y:y` is left as written: dropping the test would lose the ReferenceError an unresolvable
           read throws, and the fold pass has no binding resolution to prove the read safe (#435). The
           `x?x:y` / `x?y:x` rewrites below stay valid because they keep evaluating the test. */
        return;
    }
    if (prog->nodes[test].kind == JN_IDENT && same_expr(prog, test, cons)) {
        fold_to_logical(folder, idx, JT_OR, test, alt, depth); /* x?x:y -> x||y (a name: reading twice is safe) */
        return;
    }
    if (prog->nodes[test].kind == JN_IDENT && same_expr(prog, test, alt)) {
        fold_to_logical(folder, idx, JT_AND, test, cons, depth); /* x?y:x -> x&&y */
        return;
    }
    if (prog->nodes[cons].kind == JN_ASSIGN && prog->nodes[alt].kind == JN_ASSIGN &&
        prog->nodes[cons].op == JT_ASSIGN && prog->nodes[alt].op == JT_ASSIGN &&
        prog->nodes[prog->nodes[cons].a].kind == JN_IDENT && same_expr(prog, prog->nodes[cons].a, prog->nodes[alt].a)) {
        /* a?(t=x):(t=y) -> t=a?x:y : the same simple target is assigned either way */
        int32_t merged = make_cond(folder, test, prog->nodes[cons].b, prog->nodes[alt].b);
        if (merged < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            return;       /* GCOVR_EXCL_LINE */
        }
        prog->nodes[cons].b = merged;
        replace_with(folder, idx, cons);
    }
}

/* Rewrite a runtime `if` into a shorter equivalent (ECMA-262 14.6 selects one branch; the printer
   re-parenthesizes by precedence and re-wraps a statement-leading `{`/`function`):
     if(c) e;            -> c && e;          (no else, expression-statement consequent)
     if(c); else e;      -> c || e;          (empty consequent, expression-statement alternate)
     if(c) e1; else e2;  -> c ? e1 : e2;     (both expression statements)
   Every new node is created before any node pointer is re-read, and only indices cross the
   allocating jm_node_new call, so a realloc of the arena cannot dangle. */
static void convert_if(F *folder, int32_t idx) {
    jm_program *prog = folder->prog;
    int32_t else_branch = prog->nodes[idx].c;
    if (else_branch >= 0 && is_empty_branch(folder, prog->nodes[idx].b)) {
        int32_t else_stmt = branch_stmt(folder, else_branch);
        if (else_stmt < 0 || prog->nodes[else_stmt].kind != JN_EXPR_STMT) {
            return;
        }
        int32_t neg = jm_node_new(prog, JN_UNARY);
        if (neg < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            return;    /* GCOVR_EXCL_LINE */
        }
        prog->nodes[neg].op = JT_NOT;
        prog->nodes[neg].a = prog->nodes[idx].a;
        /* if(a){}else e -> a||e : make_logical strips the synthetic `!` and flips && to || */
        int32_t logic = make_logical(folder, neg, prog->nodes[else_stmt].a);
        if (logic < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            return;      /* GCOVR_EXCL_LINE */
        }
        prog->nodes[idx].kind = JN_EXPR_STMT;
        prog->nodes[idx].a = logic;
        prog->nodes[idx].b = -1;
        prog->nodes[idx].c = -1;
        folder->changed = 1;
        return;
    }
    int32_t then_stmt = branch_stmt(folder, prog->nodes[idx].b);
    if (then_stmt < 0) {
        return;
    }
    if (else_branch < 0) {
        if (prog->nodes[then_stmt].kind != JN_EXPR_STMT) {
            return;
        }
        int32_t logic = make_logical(folder, prog->nodes[idx].a, prog->nodes[then_stmt].a);
        if (logic < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path leaves the if as-is */
            return;      /* GCOVR_EXCL_LINE */
        }
        prog->nodes[idx].kind = JN_EXPR_STMT;
        prog->nodes[idx].a = logic;
        prog->nodes[idx].b = -1;
        prog->nodes[idx].c = -1;
        folder->changed = 1;
        return;
    }
    int32_t else_stmt = branch_stmt(folder, else_branch);
    if (else_stmt < 0) {
        return;
    }
    /* a list member's abrupt consequent had its else spliced away and fold_if_return_chain folds the
       pair; an abrupt if that is a lone branch or loop body keeps its else as written */
    if (prog->nodes[then_stmt].kind != JN_EXPR_STMT || prog->nodes[else_stmt].kind != JN_EXPR_STMT) {
        return;
    }
    int32_t cond = make_cond(folder, prog->nodes[idx].a, prog->nodes[then_stmt].a, prog->nodes[else_stmt].a);
    if (cond < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return;     /* GCOVR_EXCL_LINE */
    }
    prog->nodes[idx].kind = JN_EXPR_STMT;
    prog->nodes[idx].a = cond;
    prog->nodes[idx].b = -1;
    prog->nodes[idx].c = -1;
    folder->changed = 1;
}

/* An expression statement that may join a comma sequence. A string-literal expression statement is
   excluded so a directive prologue (`"use strict"`) is never swept into a sequence and silenced. */
static int mergeable_expr(jm_program *prog, int32_t idx) {
    return prog->nodes[idx].kind == JN_EXPR_STMT && prog->nodes[prog->nodes[idx].a].kind != JN_STRING;
}

/* The expression as a flat JN_SEQ, wrapping a non-sequence in a fresh one. -1 on allocation failure. */
static int32_t as_sequence(jm_program *prog, int32_t expr) {
    if (prog->nodes[expr].kind == JN_SEQ) {
        return expr;
    }
    int32_t seq = jm_node_new(prog, JN_SEQ);
    if (seq < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return -1; /* GCOVR_EXCL_LINE */
    }
    prog->nodes[seq].a = expr;
    return seq;
}

/* Append expr to seq's child chain, splicing a nested sequence's children so the result stays flat
   (`(a,b),c` is emitted as `a,b,c`). No allocation, so the arena base is stable. */
static void seq_append(jm_program *prog, int32_t seq, int32_t expr) {
    int32_t tail = prog->nodes[seq].a;
    while (prog->nodes[tail].next >= 0) {
        tail = prog->nodes[tail].next;
    }
    prog->nodes[tail].next = prog->nodes[expr].kind == JN_SEQ ? prog->nodes[expr].a : expr;
}

/* Comma-merge a statement list (ECMA-262 13.16, sequential evaluation, value is the last operand):
   consecutive expression statements collapse to one, and a trailing expression statement folds into
   a following `return`/`throw` argument (`a();b();return c` -> `return a(),b(),c`). Run after
   drop_unreachable: an unreachable expression merged into the init of a `for` that a declaration keeps
   would stay with it. */
static void merge_sequences(F *folder, int32_t first) {
    jm_program *prog = folder->prog;
    for (int32_t idx = first; idx >= 0; idx = prog->nodes[idx].next) {
        if (!mergeable_expr(prog, idx) || prog->nodes[idx].next < 0) {
            continue;
        }
        int32_t next = prog->nodes[idx].next;
        if (mergeable_expr(prog, next)) {
            int32_t seq = as_sequence(prog, prog->nodes[idx].a);
            if (seq < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                continue;  /* GCOVR_EXCL_LINE */
            }
            prog->nodes[idx].a = seq;
            int32_t tail = prog->nodes[seq].a;
            while (prog->nodes[tail].next >= 0) {
                tail = prog->nodes[tail].next;
            }
            while ((next = prog->nodes[idx].next) >= 0 && mergeable_expr(prog, next)) {
                int32_t expr = prog->nodes[next].a;
                prog->nodes[tail].next = prog->nodes[expr].kind == JN_SEQ ? prog->nodes[expr].a : expr;
                while (prog->nodes[tail].next >= 0) {
                    tail = prog->nodes[tail].next;
                }
                prog->nodes[idx].next = prog->nodes[next].next;
                folder->changed = 1;
            }
            next = prog->nodes[idx].next;
        }
        int32_t guard_then = next >= 0 && prog->nodes[next].kind == JN_IF && prog->nodes[next].c < 0
                                 ? branch_stmt(folder, prog->nodes[next].b)
                                 : -1;
        if (guard_then >= 0 && prog->nodes[guard_then].kind == JN_BREAK) {
            /* the test evaluates before anything else in the statement (14.6.2), exactly when the
               preceding expression statement ran, so it rides along as a comma operand. Only a
               break guard packs: it folds into the loop test or stays a statement, while any other
               if re-emerges as an expression whose sequence would cost parentheses. */
            int32_t seq = as_sequence(prog, prog->nodes[idx].a);
            if (seq < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                continue;  /* GCOVR_EXCL_LINE */
            }
            seq_append(prog, seq, prog->nodes[next].a);
            prog->nodes[next].a = seq;
            jm_node_empty(prog, idx); /* the next pass's drop_empties unlinks it */
            folder->changed = 1;
            continue;
        }
        if (next >= 0 && prog->nodes[next].kind == JN_FOR &&
            (prog->nodes[next].a < 0 || prog->nodes[prog->nodes[next].a].kind != JN_VAR)) {
            /* the init clause runs once before the first test (14.7.4.2), exactly when the
               preceding expression statement ran; a var init has no expression slot to join */
            if (prog->nodes[next].a < 0) {
                prog->nodes[next].a = prog->nodes[idx].a;
            } else {
                int32_t seq = as_sequence(prog, prog->nodes[idx].a);
                if (seq < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                    continue;  /* GCOVR_EXCL_LINE */
                }
                seq_append(prog, seq, prog->nodes[next].a);
                prog->nodes[next].a = seq;
            }
            jm_node_empty(prog, idx); /* the next pass's drop_empties unlinks it */
            folder->changed = 1;
            continue;
        }
        if (next < 0 || (prog->nodes[next].kind != JN_RETURN && prog->nodes[next].kind != JN_THROW) ||
            prog->nodes[next].a < 0) {
            continue;
        }
        int32_t seq = as_sequence(prog, prog->nodes[idx].a);
        if (seq < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            continue;  /* GCOVR_EXCL_LINE */
        }
        seq_append(prog, seq, prog->nodes[next].a);
        prog->nodes[idx].kind = prog->nodes[next].kind;
        prog->nodes[idx].a = seq;
        prog->nodes[idx].next = prog->nodes[next].next;
        folder->changed = 1;
    }
}

static void fold_if_return_chain(F *folder, int32_t first) {
    jm_program *prog = folder->prog;
    int32_t count = 0;
    for (int32_t index = first; index >= 0; index = prog->nodes[index].next) {
        count += prog->nodes[index].kind == JN_IF && prog->nodes[index].c < 0;
    }
    if (count == 0) {
        return;
    }
    int32_t local[16];
    int32_t *guards = count <= 16 ? local : jm_malloc((size_t)count * sizeof(int32_t));
    if (guards == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        prog->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;           /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int32_t written = 0;
    for (int32_t index = first; index >= 0; index = prog->nodes[index].next) {
        if (prog->nodes[index].kind == JN_IF && prog->nodes[index].c < 0) {
            guards[written++] = index;
        }
    }
    for (int32_t position = count - 1; position >= 0; position--) {
        int32_t index = guards[position];
        int32_t then = branch_stmt(folder, prog->nodes[index].b);
        int32_t next = prog->nodes[index].next;
        if (then < 0 || prog->nodes[then].kind != JN_RETURN || prog->nodes[then].a < 0 || next < 0 ||
            prog->nodes[next].kind != JN_RETURN || prog->nodes[next].a < 0) {
            continue;
        }
        if (folder->deepest >= prog->rewrite_limit) {
            break; /* this guard and the ones before it stay statements */
        }
        int32_t cond = make_cond(folder, prog->nodes[index].a, prog->nodes[then].a, prog->nodes[next].a);
        if (cond < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
            break;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        folder->deepest++; /* the test and both branches, so whatever the list held deepest, now sit one level down */
        prog->nodes[index].kind = JN_RETURN;
        prog->nodes[index].a = cond;
        prog->nodes[index].b = -1;
        prog->nodes[index].c = -1;
        prog->nodes[index].next = prog->nodes[next].next;
        folder->changed = 1;
    }
    if (guards != local) {
        jm_free(guards);
    }
}

/* A guard clause `if(c)JUMP; REST` at the top of a body whose JUMP skips the rest of that body -- a void
   `return` at a function body's top level, or a bare `continue` at a loop body's top level -- runs REST
   exactly when c is falsy, so it is `c || (REST)`. Rewrite it to `if(!c){REST}` when every following
   statement is a mergeable expression; a later fold merges that block into one comma sequence and
   convert_if turns `if(!c){seq}` into `c||seq`. Applied only at that top level: in a nested block, code
   after the block would still run, and a labeled jump could target an outer loop, so neither is folded.
   jump_kind is JN_RETURN (function body) or JN_CONTINUE (loop body). */
static void fold_guard_jump(F *folder, int32_t first, int jump_kind) {
    jm_program *prog = folder->prog;
    for (int32_t idx = first; idx >= 0; idx = prog->nodes[idx].next) {
        if (prog->nodes[idx].kind != JN_IF || prog->nodes[idx].c >= 0) {
            continue;
        }
        int32_t then = branch_stmt(folder, prog->nodes[idx].b);
        int32_t rest = prog->nodes[idx].next;
        if (then < 0 || prog->nodes[then].kind != jump_kind || rest < 0) {
            continue; /* not a bare guard jump with statements after it */
        }
        if (jump_kind == JN_RETURN ? prog->nodes[then].a >= 0 : prog->nodes[then].str != NULL) {
            continue; /* a valued return or a labeled continue is not a plain skip-the-rest */
        }
        int mergeable = 1;
        for (int32_t stmt = rest; stmt >= 0; stmt = prog->nodes[stmt].next) {
            if (!mergeable_expr(prog, stmt)) {
                mergeable = 0; /* a declaration or control-flow statement cannot move into the block */
                break;
            }
        }
        if (!mergeable) {
            continue;
        }
        int32_t neg = jm_node_new(prog, JN_UNARY);
        if (neg < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            return;    /* GCOVR_EXCL_LINE */
        }
        int32_t block = jm_node_new(prog, JN_BLOCK);
        if (block < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            return;      /* GCOVR_EXCL_LINE */
        }
        prog->nodes[neg].op = JT_NOT;
        prog->nodes[neg].a = prog->nodes[idx].a;
        prog->nodes[block].a = rest;
        prog->nodes[idx].a = neg;
        prog->nodes[idx].b = block;
        prog->nodes[idx].next = -1; /* REST now lives in the block; this if ends the chain */
        folder->changed = 1;
        return; /* one guard per pass; the next fold pass cascades a chain */
    }
}

/* A `return` ending a function body is redundant when it returns undefined: falling off the end
   already completes with undefined (10.2.1.4). A bare tail `return` is unlinked, and a tail
   `return void X` -- or `return a,b,void X` after the sequence merge -- keeps only its operands'
   evaluation, dropping a pure X (the `void 0` an `undefined` folds to) outright. */
static void fold_tail_return(F *folder, int32_t block) {
    jm_program *prog = folder->prog;
    int32_t prev = -1;
    int32_t last = prog->nodes[block].a;
    if (last < 0) {
        return;
    }
    while (prog->nodes[last].next >= 0) {
        prev = last;
        last = prog->nodes[last].next;
    }
    if (prog->nodes[last].kind != JN_RETURN) {
        return;
    }
    int32_t arg = prog->nodes[last].a;
    if (arg < 0) {
        if (prev < 0) {
            prog->nodes[block].a = -1;
        } else {
            prog->nodes[prev].next = -1;
        }
        folder->changed = 1;
        return;
    }
    int32_t value = arg; /* the returned value: a sequence returns its last element (13.16) */
    int32_t value_prev = -1;
    if (prog->nodes[arg].kind == JN_SEQ) {
        value = prog->nodes[arg].a;
        while (prog->nodes[value].next >= 0) {
            value_prev = value;
            value = prog->nodes[value].next;
        }
    }
    const jm_node *tail = &prog->nodes[value];
    if (!(tail->kind == JN_UNARY && tail->op == JT_IDENT && ident_is(tail, "void"))) {
        return;
    }
    if (!is_pure_const(folder, prog->nodes[value].a)) {
        replace_with(folder, value, prog->nodes[value].a); /* keep X's evaluation, lose the void */
    } else if (value_prev < 0) {
        if (prev < 0) { /* `return void 0` alone: the whole statement goes */
            prog->nodes[block].a = -1;
        } else {
            prog->nodes[prev].next = -1;
        }
        folder->changed = 1;
        return;
    } else { /* `return a,b,void 0`: drop the pure tail element, unwrapping a lone survivor */
        prog->nodes[value_prev].next = -1;
        if (value_prev == prog->nodes[arg].a) {
            prog->nodes[last].a = value_prev;
        }
        folder->changed = 1;
    }
    prog->nodes[last].kind = JN_EXPR_STMT;
}

/* The statement-list rewrites, in dependency order. The two merges sandwich the guard fold because
   they enable each other: the first merge turns a run before a `return` into one return so a guard
   can fold against it, the fold turns an `if` into a return, and the second merge then absorbs a
   statement that sat before that new return. drop_unreachable runs before them, on the flattened list,
   so that no merge takes in a statement it cuts. The whole sequence reaches a fixpoint, so re-minifying
   is a no-op. */
/* A block adds observable scope only for a block-scoped declaration directly in it: a `let`/`const`
   (14.2.3), a class, or a function declaration (Annex B.3.3). With none, it is transparent and its
   statements run identically spliced into the enclosing list (a `var` hoists either way), the same
   predicate the printer uses to shed a block's braces. */
static int block_is_transparent(const F *folder, int32_t block) {
    for (int32_t stmt = folder->prog->nodes[block].a; stmt >= 0; stmt = folder->prog->nodes[stmt].next) {
        int kind = folder->prog->nodes[stmt].kind;
        if (kind == JN_CLASS || kind == JN_FUNC || (kind == JN_VAR && folder->prog->nodes[stmt].decl != 0)) {
            return 0;
        }
    }
    return 1;
}

/* Normalize a statement chain in one pass: drop no-op empty statements (left by a dropped declaration,
   an `if(false)` or a bare `;`) and splice every transparent nested block's statements in place, so a
   redundant block collapses at any depth in the same call (#1066): `{{b()}}` becomes `b()`, exposing an
   enclosing `if`/loop to the folds that follow. A spliced-in statement is re-examined, so a block nested
   several deep flattens whole. Removing empties also lets the merges below see true adjacency (a binding
   the mangler drops between two `var`s no longer blocks their merge). No node is allocated, so the cached
   arena base stays valid. Returns the new head, the first surviving statement, or -1. */
static int32_t drop_empties(F *folder, int32_t first) {
    jm_node *nodes = folder->prog->nodes;
    int32_t head = -1;
    int32_t prev = -1;
    for (int32_t idx = first; idx >= 0;) {
        TH_WORK_STEP();
        int32_t next = nodes[idx].next;
        if (nodes[idx].kind == JN_EMPTY) {
            folder->changed = 1;
            idx = next;
            continue;
        }
        if (nodes[idx].kind == JN_BLOCK && block_is_transparent(folder, idx)) {
            folder->changed = 1;
            int32_t inner = nodes[idx].a;
            if (inner < 0) {
                idx = next; /* an empty block drops out entirely */
                continue;
            }
            int32_t tail = inner;
            while (nodes[tail].next >= 0) {
                tail = nodes[tail].next;
            }
            nodes[tail].next = next;
            idx = inner; /* re-examine the spliced-in statements */
            continue;
        }
        if (prev < 0) {
            head = idx;
        } else {
            nodes[prev].next = idx;
        }
        prev = idx;
        idx = next;
    }
    if (prev >= 0) {
        nodes[prev].next = -1;
    }
    return head;
}

/* An `if` whose consequent always jumps runs its alternate exactly when control would fall through,
   so the alternate splices into the statement list after the if (14.6.2). Only a list member has a
   following statement to splice before: an if that is itself a branch or loop body would orphan the
   alternate. A bare `else function f(){}` stays: promoting that Annex-B legacy form to a list
   declaration would change its scope. The walk calls this on a member once its consequent is folded
   and before it reaches the alternate, which it then walks as the next member: a redundant block
   leaves its statements to the list, and an `else if` splices in turn. Returns whether it spliced. */
static int splice_abrupt_else(F *folder, int32_t idx) {
    jm_node *nodes = folder->prog->nodes;
    int32_t alt = nodes[idx].c;
    if (!ends_abruptly(folder, nodes[idx].b) || nodes[alt].kind == JN_FUNC) {
        return 0;
    }
    nodes[idx].c = -1;
    nodes[alt].next = nodes[idx].next;
    nodes[idx].next = alt;
    if (idx < folder->hoists_cap) {
        folder->hoists[idx] = 0; /* the declaration node_hoists remembered for the if may have left with alt */
    }
    folder->changed = 1;
    return 1;
}

static void splice_lone_else(F *folder, int32_t idx);

static inline void splice_branch_else(F *folder, int32_t idx) {
    const jm_node *node = &folder->prog->nodes[idx];
    if (node->kind == JN_IF && node->c >= 0) {
        splice_lone_else(folder, idx);
    }
}

/* A single-statement body needs a block to retain the alternate after an abrupt branch. */
static void splice_lone_else(F *folder, int32_t idx) {
    jm_program *prog = folder->prog;
    int32_t alt = prog->nodes[idx].c;
    if (!ends_abruptly(folder, prog->nodes[idx].b) || prog->nodes[alt].kind == JN_FUNC) {
        return;
    }
    int32_t inner = jm_node_new(prog, JN_IF);
    if (inner < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path keeps the else */
        return;      /* GCOVR_EXCL_LINE */
    }
    prog->nodes[inner] = prog->nodes[idx];
    prog->nodes[inner].c = -1;
    prog->nodes[inner].next = alt;
    jm_node *block = &prog->nodes[idx];
    block->kind = JN_BLOCK;
    block->a = inner;
    block->b = block->c = block->d = -1;
    folder->changed = 1;
}

/* Empty static blocks have no observable effect (ECMA-262 15.7.14). */
static void drop_empty_static_blocks(F *folder, int32_t cls) {
    jm_node *nodes = folder->prog->nodes;
    int32_t prev = -1;
    for (int32_t member = nodes[cls].b; member >= 0; member = nodes[member].next) {
        if (nodes[member].decl != 4 || nodes[nodes[nodes[member].b].b].a >= 0) {
            prev = member;
        } else if (prev < 0) {
            nodes[cls].b = nodes[member].next;
            folder->changed = 1;
        } else {
            nodes[prev].next = nodes[member].next;
            folder->changed = 1;
        }
    }
}

/* Run the statement-list folds and return the list's new head. They can append nodes and so move
   prog->nodes, so a caller stores the head after the call returns, never as
   `prog->nodes[owner].a = optimize_chain(...)`, whose slot address may be taken before the call. */
static int32_t optimize_chain(F *folder, int32_t first) {
    first = drop_empties(folder, first);
    drop_unreachable(folder, first);
    first = merge_declarations(folder, first);
    merge_sequences(folder, first);
    fold_if_return_chain(folder, first);
    merge_sequences(folder, first);
    return first;
}

/* Walk the statements of the list at first, then fold the list as a whole, and return its new head (stored as
   optimize_chain's is). folder->deepest covers this list alone while it folds and joins the enclosing list's after:
   sibling lists do not count against each other, and a list sees what the lists nested in it have grown to. */
static int32_t walk_list(F *folder, int32_t first, int32_t depth) {
    int32_t outer = folder->deepest;
    folder->deepest = depth;
    walk_members(folder, first, depth + 1);
    first = optimize_chain(folder, first);
    if (outer > folder->deepest) {
        folder->deepest = outer;
    }
    return first;
}

/* Parse a plain non-negative decimal integer lexeme (digits only, no dot, exponent, separator or
   radix prefix) under 10^15 so every sum/product stays an exact IEEE-754 integer. 1 and *out set,
   or 0. */
static int int_value(const jm_node *node, uint64_t *out) {
    if (node->kind != JN_NUM || node->str_len > 15) {
        return 0;
    }
    uint64_t value = 0;
    for (Py_ssize_t index = 0; index < node->str_len; index++) {
        if (node->str[index] < '0' || node->str[index] > '9') {
            return 0;
        }
        value = value * 10 + (uint64_t)(node->str[index] - '0');
    }
    *out = value;
    return 1;
}

/* Replace the node with a decimal integer literal `value` (the printer re-canonicalises it, e.g.
   `1000` to `1e3`). The synthesized lexeme has no source span, so the program owns it. */
static void fold_int(F *folder, int32_t idx, uint64_t value) {
    Py_UCS4 digits[20];
    Py_ssize_t len = 0;
    do {
        digits[len++] = (Py_UCS4)('0' + value % 10);
        value /= 10;
    } while (value > 0);
    Py_UCS4 buf[20];
    for (Py_ssize_t index = 0; index < len; index++) {
        buf[index] = digits[len - 1 - index];
    }
    const Py_UCS4 *owned = jm_program_own(folder->prog, buf, len);
    if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation-failure path leaves the expression as-is */
        return;          /* GCOVR_EXCL_LINE */
    }
    jm_node *node = &folder->prog->nodes[idx];
    node->kind = JN_NUM;
    node->str = owned;
    node->str_len = len;
    node->a = -1;
    node->b = -1;
    folder->changed = 1;
}

/* Fold `<int> + <int>`, `<int> - <int>` (non-negative result) and `<int> * <int>` (ECMA-262 13.15);
   the integers and their result are exact, so no rounding, -0 or NaN can arise. */
static void fold_arithmetic(F *folder, int32_t idx) {
    jm_node *node = &folder->prog->nodes[idx];
    uint64_t left;
    uint64_t right;
    if (!int_value(&folder->prog->nodes[node->a], &left) || !int_value(&folder->prog->nodes[node->b], &right)) {
        return;
    }
    static const uint64_t limit = 1ull << 53; /* the largest exactly-representable IEEE-754 integer */
    if (node->op == JT_PLUS) {
        fold_int(folder, idx, left + right); /* two <10^15 operands sum to <2^53, always exact */
    } else if (node->op == JT_MINUS && left >= right) {
        fold_int(folder, idx, left - right);
    } else if (node->op == JT_STAR && (left == 0 || right <= limit / left)) {
        fold_int(folder, idx, left * right);
    }
}

/* Fold `"a" + "b"` to `"ab"` (ECMA-262 13.15.3, string concatenation is exact). The operand values
   are decoded and the result re-encoded rather than the raw lexemes spliced: a trailing variable-length
   escape (a legacy octal or a `\x`) in the left operand would otherwise absorb the right operand's first
   character and change the value (`"\1"+"2"` is `"1","2"`, not the single octal `"\12"`; #437). */
static void fold_concat(F *folder, int32_t idx, int32_t owned_mark) {
    jm_node *node = &folder->prog->nodes[idx];
    const jm_node *left = &folder->prog->nodes[node->a];
    const jm_node *right = &folder->prog->nodes[node->b];
    if (left->kind != JN_STRING || right->kind != JN_STRING || left->str[0] != right->str[0]) {
        return; /* same-quote operands only, so the result keeps that quote and re-encoding stays minimal */
    }
    Py_UCS4 quote = left->str[0];
    Py_ssize_t value_cap = left->str_len + right->str_len; /* neither decoded value outgrows its lexeme */
    /* one scratch block: the decoded value, then the lexeme re-encoded after it (the widest escape is 6 wide) */
    Py_UCS4 *scratch = jm_malloc((size_t)(value_cap * 7 + 2) * sizeof(Py_UCS4));
    if (scratch == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        folder->prog->failed = 1; /* GCOVR_EXCL_LINE */
        return;                   /* GCOVR_EXCL_LINE */
    }
    Py_UCS4 *value = scratch;
    Py_ssize_t value_len = jm_str_decode(left->str, left->str_len, value);
    Py_ssize_t right_len = value_len < 0 ? -1 : jm_str_decode(right->str, right->str_len, value + value_len);
    if (right_len < 0) { /* an operand with an escape that has no value is left as written */
        jm_free(scratch);
        return;
    }
    value_len += right_len;
    Py_UCS4 *lexeme = scratch + value_cap;
    lexeme[0] = quote;
    Py_ssize_t inner = jm_str_encode(value, value_len, quote, lexeme + 1);
    lexeme[inner + 1] = quote;
    const Py_UCS4 *owned = jm_program_own(folder->prog, lexeme, inner + 2);
    jm_free(scratch);
    if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return;          /* GCOVR_EXCL_LINE */
    }
    /* Every lexeme owned since the walk entered this node was synthesized for a node below it, and the rewrite
       unlinks all of them. Keeping them until the program is freed would make an n-literal chain hold n/2 copies of
       its joined value. */
    jm_program_disown(folder->prog, owned_mark);
    node = &folder->prog->nodes[idx];
    node->kind = JN_STRING;
    node->str = owned;
    node->str_len = inner + 2;
    node->a = -1;
    node->b = -1;
    folder->changed = 1;
}

/* Fold a logical node against its folded operands. deepest_a is the deepest point under its left operand. */
static void fold_logical(F *folder, int32_t idx, int32_t deepest_a) {
    const jm_node *node = &folder->prog->nodes[idx];
    if (node->op == JT_NULLISH) {
        if (is_nullish(folder, node->a)) {
            replace_with(folder, idx, node->b);
        } else if (is_pure_const(folder, node->a)) {
            replace_with(folder, idx, node->a);
        } else {
            rotate_logical_chain(folder, idx, node->a, deepest_a);
        }
        return;
    }
    int truth = pure_truthy(folder, node->a);
    if (truth < 0) {
        rotate_logical_chain(folder, idx, node->a, deepest_a);
        return;
    }
    int keep_right = node->op == JT_AND ? truth : !truth;
    replace_with(folder, idx, keep_right ? node->b : node->a);
}

/* Walk an assignment, update or for-in/of target. A bare name there is the Reference being written,
   so `undefined` stays a name: `void 0=1` is an early error (13.15.1). A pattern's default values and
   computed keys are ordinary expressions. */
static void walk_target(F *folder, int32_t idx, int32_t depth) {
    const jm_node *node = &folder->prog->nodes[idx];
    int32_t child_a = node->a;
    int32_t child_b = node->b;
    switch (node->kind) {
    case JN_IDENT:
        return;
    case JN_ARRAY:
    case JN_OBJECT:
        for (int32_t element = child_a; element >= 0; element = folder->prog->nodes[element].next) {
            walk_target(folder, element, depth);
        }
        return;
    case JN_PROP:
        if (node->flags & JN_F_COMPUTED) {
            walk(folder, child_a, depth);
        }
        if (child_b >= 0) {
            walk_target(folder, child_b, depth);
        }
        return;
    case JN_SPREAD:
        walk_target(folder, child_a, depth);
        return;
    case JN_ASSIGN:
        walk_target(folder, child_a, depth);
        walk(folder, child_b, depth);
        return;
    default:
        walk(folder, idx, depth);
        return;
    }
}

/* A `+` walks its operands, then joins them if both are strings. The buffers the operands' folds made are released by
   that join, so the count taken here marks where this subtree's buffers start. */
static TH_NOINLINE void walk_plus(F *folder, int32_t idx, int32_t depth) {
    int32_t child_a = folder->prog->nodes[idx].a;
    int32_t child_b = folder->prog->nodes[idx].b;
    int32_t owned_mark = folder->prog->owned_count;
    walk(folder, child_a, depth + 1);
    walk(folder, child_b, depth + 1);
    fold_concat(folder, idx, owned_mark);
}

static TH_NOINLINE void walk_block(F *folder, int32_t idx, int32_t depth, int member) {
    int32_t outer = folder->deepest;
    folder->deepest = depth;
    walk_members(folder, folder->prog->nodes[idx].a, depth + 1);
    /* a transparent member's statements fold with the enclosing list, where its drop_empties splices them;
       folding them here too would go over the innermost statements of a `{{{...}}}` nest once per level */
    if (!member || !block_is_transparent(folder, idx)) {
        int32_t first = optimize_chain(folder, folder->prog->nodes[idx].a);
        folder->prog->nodes[idx].a = first;
    }
    if (outer > folder->deepest) {
        folder->deepest = outer;
    }
}

static TH_NOINLINE void walk_if(F *folder, int32_t idx, int32_t depth, int member) {
    int32_t child_a = folder->prog->nodes[idx].a;
    int32_t child_b = folder->prog->nodes[idx].b;
    int32_t child_c = folder->prog->nodes[idx].c;
    int32_t outer = folder->deepest;
    folder->deepest = depth;
    walk(folder, child_a, depth + 1);
    int truth = pure_truthy(folder, child_a);
    int32_t taken = truth ? child_b : child_c;
    int32_t dropped = truth ? child_c : child_b;
    /* A constant test leaves only the taken branch, unless the dropped one declares a hoisted name or the
       taken one is the Annex-B legacy `if(x) function f(){}`, whose promotion would change its scope. The
       branch takes the if's place before it is walked, so in a statement list it is walked as the member
       the list splices in, not as a branch folded on its own. */
    if (truth >= 0 && !node_hoists(folder, dropped) && (taken < 0 || folder->prog->nodes[taken].kind != JN_FUNC)) {
        if (taken < 0) {
            jm_node_empty(folder->prog, idx); /* if(false) with no else */
            folder->changed = 1;
        } else {
            replace_with(folder, idx, taken);
            walk_stmt(folder, idx, depth, member);
        }
    } else {
        walk(folder, child_b, depth + 1);
        splice_branch_else(folder, child_b);
        if (child_c >= 0 && !(member && splice_abrupt_else(folder, idx))) {
            walk(folder, child_c, depth + 1);
            splice_branch_else(folder, child_c); /* the walks and splices may move the node arena */
        }
        if (folder->prog->nodes[idx].c >= 0 && is_empty_branch(folder, folder->prog->nodes[idx].c)) {
            folder->prog->nodes[idx].c = -1; /* an empty else does nothing: if(a)b();else{} -> if(a)b() -> a&&b() */
            folder->changed = 1;
        }
        if (truth < 0) {
            convert_if(folder, idx);
        }
    }
    if (outer > folder->deepest) {
        folder->deepest = outer;
    }
}

static inline void walk_stmt(F *folder, int32_t idx, int32_t depth, int member) {
    int kind = folder->prog->nodes[idx].kind;
    if (member && kind == JN_IF) {
        walk_if(folder, idx, depth, 1);
    } else if (member && kind == JN_BLOCK) {
        walk_block(folder, idx, depth, 1);
    } else {
        walk(folder, idx, depth);
    }
}

/* Fold the subtree at idx, which sits depth walk calls under the program root. folder->deepest is the deepest point
   the walk has reached under the innermost if, logical or statement list around it: the guard fold and the chain
   rotation read it to know how tall the tree they are about to grow already is. */
static void walk(F *folder, int32_t idx, int32_t depth) {
    if (idx < 0) {
        return;
    }
    if (depth > folder->deepest) {
        folder->deepest = depth;
    }
    jm_node *node = &folder->prog->nodes[idx];
    int kind = node->kind;
    switch (kind) {
    case JN_NUM:
    case JN_STRING:
    case JN_REGEX:
    case JN_BIGINT:
    case JN_EMPTY:
    case JN_DEBUGGER:
        return; /* leaves: nothing to walk and no transform applies, so skip the four child calls */
    case JN_IDENT:
        if (ident_is(node, "true")) {
            fold_boolean(folder, idx, 1);
        } else if (ident_is(node, "false")) {
            fold_boolean(folder, idx, 0);
        } else if (ident_is(node, "undefined") && folder->with_depth == 0 &&
                   (!folder->prog->shadows_undefined || (folder->prog->resolved && node->sym < 0))) {
            fold_void(folder, idx); /* a read no binding shadows is the global undefined, void 0 */
        }
        return;
    default:
        break;
    }
    uint32_t flags = node->flags;
    int32_t child_a = node->a;
    int32_t child_b = node->b;
    int32_t child_c = node->c;
    int32_t child_d = node->d;
    /* recurse into children first (post-order), skipping property-name positions. The kind, flags
       and child indices are captured before any recursion: a fold inside a child allocates nodes,
       which may grow the arena and move it, so `node` dangles the moment a child walk returns.
       A child walk rewrites nodes in place and never edits its parent, so the captures stay true. */
    switch (kind) {
    case JN_WITH:
        walk(folder, child_a, depth + 1);
        folder->with_depth++;
        walk(folder, child_b, depth + 1);
        folder->with_depth--;
        splice_branch_else(folder, child_b);
        return;
    case JN_ASSIGN:
        walk_target(folder, child_a, depth + 1);
        walk(folder, child_b, depth + 1);
        return;
    case JN_UPDATE:
        walk_target(folder, child_a, depth + 1);
        return;
    case JN_MEMBER_EXPR:
        walk(folder, child_a, depth + 1);
        if (flags & JN_F_COMPUTED) {
            walk(folder, child_b, depth + 1);
        }
        return;
    case JN_PROP:
    case JN_MEMBER:
        if (flags & JN_F_COMPUTED) {
            walk(folder, child_a, depth + 1);
        }
        walk(folder, child_b, depth + 1);
        return;
    case JN_ARRAY:
    case JN_OBJECT:
    case JN_SEQ:
    case JN_TEMPLATE:
        walk_chain(folder, child_a, depth + 1);
        return;
    case JN_CALL:
    case JN_NEW:
        walk(folder, child_a, depth + 1);
        walk_chain(folder, child_b, depth + 1);
        return;
    case JN_CLASS:
        walk(folder, child_a, depth + 1);
        walk_chain(folder, child_b, depth + 1);
        drop_empty_static_blocks(folder, idx);
        return;
    case JN_SWITCH:
        walk(folder, child_a, depth + 1);
        for (int32_t clause = child_b; clause >= 0; clause = folder->prog->nodes[clause].next) {
            walk(folder, folder->prog->nodes[clause].a, depth + 1);
            int32_t consequent = walk_list(folder, folder->prog->nodes[clause].b, depth);
            folder->prog->nodes[clause].b = consequent;
        }
        return;
    case JN_EXPR_STMT:
        walk(folder, child_a, depth + 1);
        /* a constant has no side effect, so the statement only matters as a directive (11.2.1) */
        if (is_pure_const(folder, child_a) && !(folder->prog->nodes[idx].flags & JN_F_DIRECTIVE)) {
            jm_node_empty(folder->prog, idx);
            folder->changed = 1;
        }
        return;
    case JN_VAR:
        for (int32_t declr = child_a; declr >= 0; declr = folder->prog->nodes[declr].next) {
            walk(folder, folder->prog->nodes[declr].b, depth + 1);
        }
        return;
    case JN_BLOCK:
        walk_block(folder, idx, depth, 0);
        return;
    case JN_FUNC:
    case JN_ARROW:
        walk_chain(folder, child_a, depth + 1); /* params (default values may fold) */
        walk(folder, child_b, depth + 1);       /* body */
        if (!(kind == JN_ARROW && (flags & JN_F_EXPRBODY))) {
            /* a block body: its statements run to the function's end, so a trailing undefined-valued
               return is redundant and a guard-return there folds */
            fold_tail_return(folder, child_b);
            fold_guard_jump(folder, folder->prog->nodes[child_b].a, JN_RETURN);
        }
        return;
    case JN_FOR:
    case JN_FORIN:
    case JN_FOROF:
    case JN_WHILE:
    case JN_DOWHILE: {
        if (kind == JN_FORIN || kind == JN_FOROF) {
            walk_target(folder, child_a, depth + 1);
        } else {
            walk(folder, child_a, depth + 1);
        }
        walk(folder, child_b, depth + 1);
        walk(folder, child_c, depth + 1);
        walk(folder, child_d, depth + 1);
        /* a bare `continue` at a loop body's top level skips the rest of that body, so a guard there
           folds like a function's guard-return. The body field differs per loop kind. */
        int32_t body = kind == JN_DOWHILE ? child_a
                       : kind == JN_WHILE ? child_b
                       : kind == JN_FOR   ? child_d
                                          : child_c; /* for-in / for-of */
        splice_branch_else(folder, body);
        if (folder->prog->nodes[body].kind == JN_BLOCK) { /* a loop always has a body statement */
            fold_guard_jump(folder, folder->prog->nodes[body].a, JN_CONTINUE);
        }
        if (kind == JN_WHILE) {
            /* while(c) and for(;c;) run identically (14.7.3 / 14.7.4: test before every iteration,
               a continue re-tests through the empty update), and only the for form has the init
               slot a preceding expression statement merges into. Re-read node: the walks and the
               guard fold above may have grown the arena. */
            node = &folder->prog->nodes[idx];
            node->kind = JN_FOR;
            node->a = -1;
            node->b = child_a;
            node->c = -1;
            node->d = child_b;
            folder->changed = 1;
        }
        node = &folder->prog->nodes[idx];
        if (node->kind == JN_FOR && node->a >= 0 && folder->prog->nodes[node->a].kind == JN_EMPTY) {
            node->a = -1; /* a dropped declaration left an empty init; clear it so a var can merge in */
            folder->changed = 1;
        }
        if (node->kind == JN_FOR && node->b >= 0 && pure_truthy(folder, node->b) == 1) {
            node->b = -1; /* an absent test always continues (14.7.4.2), same as a truthy constant */
            folder->changed = 1;
        }
        if (node->kind == JN_FOR) {
            /* `for(;T;U){if(C)break;R}` runs R exactly when T then !C hold, and the break skips the
               update just as a failed test would never reach it, so the guard folds into the test:
               `for(;T&&!C;U){R}`. Only a bare unlabeled break at the body's top qualifies -- a
               labeled break may target an outer loop, and a later position runs code before C. */
            int body_is_block = folder->prog->nodes[body].kind == JN_BLOCK;
            int32_t guard = body_is_block ? folder->prog->nodes[body].a : body;
            if (guard >= 0 && folder->prog->nodes[guard].kind == JN_IF && folder->prog->nodes[guard].c < 0) {
                int32_t then = branch_stmt(folder, folder->prog->nodes[guard].b);
                if (then >= 0 && folder->prog->nodes[then].kind == JN_BREAK && folder->prog->nodes[then].str == NULL) {
                    int32_t negated = negate_for_test(folder, folder->prog->nodes[guard].a);
                    if (negated >= 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path keeps the if */
                        node = &folder->prog->nodes[idx];
                        if (node->b < 0) {
                            node->b = negated;
                        } else {
                            int32_t logic = jm_node_new(folder->prog, JN_LOGICAL);
                            if (logic < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                                return;      /* GCOVR_EXCL_LINE */
                            }
                            node = &folder->prog->nodes[idx];
                            folder->prog->nodes[logic].op = JT_AND;
                            folder->prog->nodes[logic].a = node->b;
                            folder->prog->nodes[logic].b = negated;
                            node->b = logic;
                        }
                        if (body_is_block) {
                            folder->prog->nodes[body].a = folder->prog->nodes[guard].next;
                        }
                        if (!body_is_block || folder->prog->nodes[body].a < 0) {
                            jm_node_empty(folder->prog, body); /* prints as the bare `;` body */
                        }
                        folder->changed = 1;
                    }
                }
            }
        }
        return;
    }
    case JN_LOGICAL: {
        /* the rotation and the if-to-logical rewrite read how deep this node's own subtree reaches */
        int32_t outer = folder->deepest;
        folder->deepest = depth;
        walk(folder, child_a, depth + 1);
        int32_t deepest_a = folder->deepest; /* the deepest point under the first child */
        walk(folder, child_b, depth + 1);
        walk(folder, child_c, depth + 1);
        walk(folder, child_d, depth + 1);
        fold_logical(folder, idx, deepest_a);
        if (outer > folder->deepest) {
            folder->deepest = outer;
        }
        return;
    }
    case JN_IF:
        walk_if(folder, idx, depth, 0);
        return;
    case JN_UNARY:
        /* `delete undefined` deletes the global binding, which is non-configurable (false, 13.5.1.2);
           `delete void 0` deletes a value (true) */
        if (node->op != JT_IDENT || !ident_is(node, "delete") || folder->prog->nodes[child_a].kind != JN_IDENT) {
            walk(folder, child_a, depth + 1);
        }
        break;
    case JN_BINARY:
        if (node->op == JT_PLUS) {
            walk_plus(folder, idx, depth); /* a `+` has no `c` child: only the word operators carry one */
            break;
        }
        /* falls through */
    default:
        walk(folder, child_a, depth + 1);
        walk(folder, child_b, depth + 1);
        walk(folder, child_c, depth + 1);
        walk(folder, child_d, depth + 1);
        break;
    }
    /* then transform this node against its now-folded children */
    node = &folder->prog->nodes[idx];
    switch (node->kind) {
    case JN_UNARY:
        if (node->op == JT_NOT) {
            const jm_node *operand = &folder->prog->nodes[node->a];
            if (operand->kind == JN_NUM && operand->str_len == 1 &&
                (operand->str[0] == '0' || operand->str[0] == '1')) {
                return;
            }
            int truth = pure_truthy(folder, node->a);
            if (truth >= 0) {
                fold_boolean(folder, idx, !truth);
                return;
            }
            if (operand->kind == JN_BINARY && (operand->op == JT_EQ_EQ || operand->op == JT_NE ||
                                               operand->op == JT_EQ_EQ_EQ || operand->op == JT_NE_EQ)) {
                /* !(a==b) and a!=b are the same boolean everywhere, not just in a test position
                   (7.2.13/7.2.14 define one as the other's exact complement, both booleans) */
                folder->prog->nodes[node->a].op = operand->op == JT_EQ_EQ      ? JT_NE
                                                  : operand->op == JT_NE       ? JT_EQ_EQ
                                                  : operand->op == JT_EQ_EQ_EQ ? JT_NE_EQ
                                                                               : JT_EQ_EQ_EQ;
                replace_with(folder, idx, node->a);
            }
        }
        return;
    case JN_COND: {
        int truth = pure_truthy(folder, node->a);
        if (truth >= 0) {
            replace_with(folder, idx, truth ? node->b : node->c);
            return;
        }
        int32_t test = node->a;
        if (negate_test(folder, &test) % 2) { /* !x?a:b -> x?b:a; a cheaper negation swaps too */
            node = &folder->prog->nodes[idx]; /* negate_test allocates; re-read */
            int32_t swap = node->b;
            node->b = node->c;
            node->c = swap;
        }
        folder->prog->nodes[idx].a = test;
        fold_conditional(folder, idx, depth);
        return;
    }
    case JN_LABEL:
        splice_branch_else(folder, node->a);
        return;
    case JN_BINARY:
        fold_arithmetic(folder, idx); /* integer operands; a folded concat is already done */
        if (node->op == JT_EQ_EQ || node->op == JT_NE || node->op == JT_EQ_EQ_EQ || node->op == JT_NE_EQ) {
            int32_t typeof_side = is_typeof_nonref(folder, node->a)   ? node->a
                                  : is_typeof_nonref(folder, node->b) ? node->b
                                                                      : -1;
            int32_t other_side = typeof_side == node->a ? node->b : node->a;
            if (typeof_side >= 0 && string_is(&folder->prog->nodes[other_side], "undefined")) {
                /* `typeof X op "undefined"` is true exactly when X's value is undefined (13.5.3),
                   so it shortens to the strict `void 0===X` / `void 0!==X`; X's evaluation (and any
                   throw from it) is identical on both forms since X is not a bare name. */
                int32_t undef = jm_node_new(folder->prog, JN_UNARY);
                if (undef < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path keeps the typeof */
                    return;      /* GCOVR_EXCL_LINE */
                }
                fold_void(folder, undef);
                node = &folder->prog->nodes[idx];             /* the two allocations may have moved the arena */
                if (folder->prog->nodes[undef].str == NULL) { /* GCOVR_EXCL_BR_LINE: alloc failure */
                    return;                                   /* GCOVR_EXCL_LINE */
                }
                node->op = node->op == JT_EQ_EQ || node->op == JT_EQ_EQ_EQ ? JT_EQ_EQ_EQ : JT_NE_EQ;
                node->a = undef;
                node->b = folder->prog->nodes[typeof_side].a;
                return;
            }
        }
        if ((node->op == JT_EQ_EQ_EQ || node->op == JT_NE_EQ) && static_type(folder, node->a) != 0 &&
            static_type(folder, node->a) == static_type(folder, node->b)) {
            /* same-type operands make loose equality strict (7.2.14 step 1), so === weakens to ==
               without a behavior change: typeof x==="string" -> typeof x=="string" */
            node->op = node->op == JT_EQ_EQ_EQ ? JT_EQ_EQ : JT_NE;
            folder->changed = 1;
        }
        return;
    default:
        return;
    }
}

int jm_fold(jm_program *prog) {
    F folder = {.prog = prog, .changed = 0};
    /* with a binding named `undefined` anywhere, only a read resolution proves global folds. The passes move and
       drop bindings but never create one, so once none is named `undefined` a later fold need not look again. */
    if (!prog->never_undefined) {
        prog->shadows_undefined = declares(&folder, prog->nodes[prog->root].a, "undefined");
        prog->never_undefined = !prog->shadows_undefined;
    }
    /* the transforms cascade -- a spliced else exposes a guard fold, whose block a merge then
       collapses -- so walk until a full pass changes nothing and the output is a fixpoint */
    int any = 0;
    /* GCOVR_EXCL_BR_START: converges well before the backstop cap */
    for (int pass = 0; pass < 12; pass++) {
        /* GCOVR_EXCL_BR_STOP */
        folder.changed = 0;
        folder.pass = pass + 1;
        int32_t first = walk_list(&folder, prog->nodes[prog->root].a, 0);
        prog->nodes[prog->root].a = first;
        if (!folder.changed) {
            break;
        }
        any = 1;
    }
    jm_free(folder.hoists);
    return any;
}
