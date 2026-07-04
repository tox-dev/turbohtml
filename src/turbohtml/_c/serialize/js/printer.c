/* Prints the arena AST back out as minified JavaScript.

   Three rules carry the correctness of a single-line render:
   - an adjacency guard inserts the one space that keeps two tokens from merging (so
     `return x`, `a in b`, `a+ +b`, `a/ /re/` stay separate) - driven purely by the
     last emitted code point, not by per-keyword special cases;
   - statements are separated by an explicit `;`, with the trailing one before a `}`
     or end of input dropped (the deferred-semicolon idea), so joining onto one line
     never triggers the leading-token continuation hazard;
   - parentheses are re-inserted only where operator precedence requires them, plus
     the statement-start guard that wraps an expression statement opening with `{`,
     `function` or `class`.

   Numeric literals are canonicalized to the shortest value-preserving form; string
   quote/escape minimization and constant folding are later phases. */

#include "serialize/js/internal.h"

#include <stdio.h>
#include <string.h>

typedef struct {
    const jm_program *prog;
    Py_UCS4 *data;
    Py_ssize_t len;
    Py_ssize_t cap;
    Py_UCS4 last;    /* the last code point emitted, for the adjacency guard */
    Py_UCS4 prev;    /* the one before last, to break the <!-- and --> comment markers */
    int after_regex; /* the last token was a regex literal: an identifier after it
                        would be read as further regex flags, so it needs a space */
    int no_in;       /* printing a for-loop init: a top-level `in` needs parentheses
                        or it would read as the for-in form */
    int failed;
} St;

static int is_id_char(Py_UCS4 ch) {
    return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '_' || ch == '$' ||
           ch >= 0x80;
}

static int ascii_id_part(Py_UCS4 ch) {
    return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '_' || ch == '$';
}

/* The inner code points of a string literal that spells an escape-free ASCII IdentifierName, else
   NULL. Lets `a["x"]` print as `a.x` and an object key `{"x":1}` as `{x:1}` (ECMA-262 13.2.5,
   13.3.2). Restricted to ASCII so a >= 0x80 code point that is not an IdentifierStart is never
   emitted bare, and a backslash (an escape) fails the test, so the decoded value always matches. */
static const Py_UCS4 *string_ident(const jm_node *node, Py_ssize_t *out_len) {
    if (node->kind != JN_STRING || node->str_len < 3) {
        return NULL;
    }
    const Py_UCS4 *inner = node->str + 1;
    Py_ssize_t len = node->str_len - 2;
    if (inner[0] >= '0' && inner[0] <= '9') {
        return NULL; /* an IdentifierStart is never a digit */
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (!ascii_id_part(inner[index])) {
            return NULL;
        }
    }
    *out_len = len;
    return inner;
}

/* `{"__proto__": v}` sets the prototype while `{__proto__: v}` would too, but the quoted form is an
   ordinary own property -- so the quotes on a __proto__ data key are load-bearing and never dropped. */
static int is_proto_key(const Py_UCS4 *name, Py_ssize_t len) {
    static const char proto[] = "__proto__";
    if (len != 9) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < 9; index++) {
        if (name[index] != (Py_UCS4)proto[index]) {
            return 0;
        }
    }
    return 1;
}

/* Would emitting `next` straight after `prev` merge them into one different token? */
static int would_merge(Py_UCS4 prev, Py_UCS4 next) {
    if (is_id_char(prev) && is_id_char(next)) {
        return 1; /* two identifier/number runs, or keyword+name */
    }
    if ((prev == '+' && next == '+') || (prev == '-' && next == '-')) {
        return 1; /* + +  /  ++ +  would read as ++ / +++ */
    }
    if (prev == '/' && next == '/') {
        return 1; /* a division followed by a regex would open a comment */
    }
    return 0;
}

static void grow(St *st, Py_ssize_t extra) {
    if (st->len + extra <= st->cap) {
        return;
    }
    Py_ssize_t cap = st->cap ? st->cap : 256;
    while (cap < st->len + extra) {
        cap *= 2;
    }
    Py_UCS4 *grown = jm_realloc(st->data, (size_t)cap * sizeof(Py_UCS4));
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        st->failed = 1;  /* GCOVR_EXCL_LINE */
        return;          /* GCOVR_EXCL_LINE */
    }
    st->data = grown;
    st->cap = cap;
}

/* Append a run of code points, inserting a guard space first when the boundary would
   merge. All other emitters funnel through here so the guard is never bypassed. */
static int needs_guard(const St *st, Py_UCS4 next) {
    if (st->len == 0) {
        return 0;
    }
    if (st->after_regex && is_id_char(next)) {
        return 1;
    }
    /* break the HTML-style comment markers `<!--` and `-->`, which Annex B reads as
       line comments: a space after `<!` before `-`, and after `--` before `>`. */
    if (next == '-' && st->last == '!' && st->prev == '<') {
        return 1;
    }
    if (next == '>' && st->last == '-') { /* the printer emits `-` before `>` only as `-->` */
        return 1;
    }
    return would_merge(st->last, next);
}

/* Recompute the trailing-two-code-point window after an append. Only ever called after
   appending at least one code point, so st->len >= 1. */
static void sync_tail(St *st) {
    st->last = st->data[st->len - 1];
    st->prev = st->len > 1 ? st->data[st->len - 2] : 0;
}

static void put_run(St *st, const Py_UCS4 *text, Py_ssize_t len) {
    if (needs_guard(st, text[0])) {
        grow(st, 1);
        if (st->failed) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            return;       /* GCOVR_EXCL_LINE */
        }
        st->data[st->len++] = ' ';
    }
    st->after_regex = 0;
    grow(st, len);
    if (st->failed) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return;       /* GCOVR_EXCL_LINE */
    }
    memcpy(st->data + st->len, text, (size_t)len * sizeof(Py_UCS4));
    st->len += len;
    sync_tail(st);
}

static void put_ascii(St *st, const char *str) {
    Py_ssize_t len = (Py_ssize_t)strlen(str); /* every token the printer emits is a fixed
                                                 keyword/operator literal; `instanceof`,
                                                 at 10, is the longest, so 32 never spills */
    Py_UCS4 stackbuf[32];
    for (Py_ssize_t index = 0; index < len; index++) {
        stackbuf[index] = (Py_UCS4)(unsigned char)str[index];
    }
    put_run(st, stackbuf, len);
}

/* Emit a structural code point ( ) { } ; , etc. None can merge with a preceding token
   (a separator never combines into a longer one), so no adjacency guard is needed. */
static void put_char(St *st, Py_UCS4 ch) {
    st->after_regex = 0;
    grow(st, 1);
    if (st->failed) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return;       /* GCOVR_EXCL_LINE */
    }
    st->data[st->len++] = ch;
    sync_tail(st);
}

static int op_prec(uint16_t op) {
    switch (op) { /* GCOVR_EXCL_BR_LINE: exhaustive switch; default unreachable */
    case JT_NULLISH:
        return 4;
    case JT_OR:
        return 5;
    case JT_AND:
        return 6;
    case JT_BIT_OR:
        return 7;
    case JT_BIT_XOR:
        return 8;
    case JT_BIT_AND:
        return 9;
    case JT_EQ_EQ:
    case JT_NE:
    case JT_EQ_EQ_EQ:
    case JT_NE_EQ:
        return 10;
    case JT_LT:
    case JT_GT:
    case JT_LE:
    case JT_GE:
    case JT_IDENT:
        return 11; /* in / instanceof */
    case JT_SHL:
    case JT_SHR:
    case JT_USHR:
        return 12;
    case JT_PLUS:
    case JT_MINUS:
        return 13;
    case JT_STAR:
    case JT_DIV:
    case JT_MOD:
        return 14;
    case JT_POW:
        return 15;
    default:      /* GCOVR_EXCL_LINE: op_prec is only called on binary/logical ops */
        return 0; /* GCOVR_EXCL_LINE */
    }
}

static int node_prec(const jm_program *prog, int32_t index) {
    const jm_node *node = &prog->nodes[index];
    switch (node->kind) {
    case JN_SEQ:
        return 1;
    case JN_ASSIGN:
    case JN_ARROW:
    case JN_YIELD:
        return 2;
    case JN_COND:
        return 3;
    case JN_BINARY:
    case JN_LOGICAL:
        return op_prec(node->op);
    case JN_UNARY:
    case JN_AWAIT:
        return 16;
    case JN_UPDATE:
        return (node->flags & JN_F_PREFIX) ? 16 : 17;
    case JN_NEW:
    case JN_CALL:
    case JN_MEMBER_EXPR:
    case JN_TAGGED:
        return 18; /* new / call / member / tagged all bind at the left-hand-side level */
    default:
        return 20; /* primaries: literals, identifiers, arrays, objects, parens */
    }
}

/* A `new` constructor is a MemberExpression, which excludes a call or a tagged
   template anywhere along its object spine: `new a(b).c(d)` would bind the `(b)` to
   `new`, so a callee whose spine contains one must be parenthesized. */
static int new_callee_needs_parens(const jm_program *prog, int32_t index) {
    for (;;) {
        const jm_node *node = &prog->nodes[index];
        if (node->kind == JN_CALL || node->kind == JN_TAGGED) {
            return 1;
        }
        if (node->kind == JN_MEMBER_EXPR) {
            index = node->a;
            continue;
        }
        return 0;
    }
}

static void print_expr(St *st, int32_t index);
static int print_stmt(St *st, int32_t index);
static void print_block(St *st, int32_t index);
static void print_function(St *st, int32_t index, int as_method);

/* Print an expression, wrapping it in parentheses when its precedence is below the
   context's minimum (so only the necessary parens survive). */
static void print_sub(St *st, int32_t index, int min_prec) {
    if (node_prec(st->prog, index) < min_prec) {
        put_char(st, '(');
        print_expr(st, index);
        put_char(st, ')');
    } else {
        print_expr(st, index);
    }
}

/* Print a logical/coalesce operand. ECMA-262 §13.13 forbids ?? adjacent to an unparenthesized
   || or && (a Syntax early error), so the source parens around `(a||b)??c` or `a??(b&&c)` are
   load-bearing; the parser keeps no parens once the tree is built, so re-add them whenever a
   coalesce meets a logical-and/or. Every other operand follows the precedence rule. */
static void print_mix_operand(St *st, uint16_t parent_op, int32_t child, int min_prec) {
    const jm_node *node = &st->prog->nodes[child];
    if (node->kind == JN_LOGICAL) {
        int coalesce_over_logic = parent_op == JT_NULLISH && (node->op == JT_OR || node->op == JT_AND);
        int logic_over_coalesce = (parent_op == JT_OR || parent_op == JT_AND) && node->op == JT_NULLISH;
        if (coalesce_over_logic || logic_over_coalesce) {
            put_char(st, '(');
            print_expr(st, child);
            put_char(st, ')');
            return;
        }
    }
    print_sub(st, child, min_prec);
}

static const char *binary_op_str(uint16_t op) {
    switch (op) { /* GCOVR_EXCL_BR_LINE: exhaustive switch; default unreachable */
    case JT_NULLISH:
        return "??";
    case JT_OR:
        return "||";
    case JT_AND:
        return "&&";
    case JT_BIT_OR:
        return "|";
    case JT_BIT_XOR:
        return "^";
    case JT_BIT_AND:
        return "&";
    case JT_EQ_EQ:
        return "==";
    case JT_NE:
        return "!=";
    case JT_EQ_EQ_EQ:
        return "===";
    case JT_NE_EQ:
        return "!==";
    case JT_LT:
        return "<";
    case JT_GT:
        return ">";
    case JT_LE:
        return "<=";
    case JT_GE:
        return ">=";
    case JT_SHL:
        return "<<";
    case JT_SHR:
        return ">>";
    case JT_USHR:
        return ">>>";
    case JT_PLUS:
        return "+";
    case JT_MINUS:
        return "-";
    case JT_STAR:
        return "*";
    case JT_DIV:
        return "/";
    case JT_MOD:
        return "%";
    case JT_POW:
        return "**";
    default:        /* GCOVR_EXCL_LINE: binary_op_str is only called on the operators above */
        return "?"; /* GCOVR_EXCL_LINE */
    }
}

static const char *assign_op_str(uint16_t op) {
    switch (op) { /* GCOVR_EXCL_BR_LINE: exhaustive switch; default unreachable */
    case JT_ASSIGN:
        return "=";
    case JT_PLUS_ASSIGN:
        return "+=";
    case JT_MINUS_ASSIGN:
        return "-=";
    case JT_STAR_ASSIGN:
        return "*=";
    case JT_DIV_ASSIGN:
        return "/=";
    case JT_MOD_ASSIGN:
        return "%=";
    case JT_POW_ASSIGN:
        return "**=";
    case JT_SHL_ASSIGN:
        return "<<=";
    case JT_SHR_ASSIGN:
        return ">>=";
    case JT_USHR_ASSIGN:
        return ">>>=";
    case JT_AND_ASSIGN:
        return "&=";
    case JT_OR_ASSIGN:
        return "|=";
    case JT_XOR_ASSIGN:
        return "^=";
    case JT_LAND_ASSIGN:
        return "&&=";
    case JT_LOR_ASSIGN:
        return "||=";
    case JT_NULLISH_ASSIGN:
        return "?\?="; /* the escaped ? avoids the ??= C trigraph for # */
    default:           /* GCOVR_EXCL_LINE: assign_op_str is only called on assignment ops */
        return "=";    /* GCOVR_EXCL_LINE */
    }
}

static void print_text(St *st, const jm_node *node) {
    /* a renamed binding or reference emits its mangled name; everything else (literals,
       property names, kept identifiers) emits its borrowed source span. A renamed named
       function/class expression carries its mangled name on the JN_FUNC/JN_CLASS node. */
    if (node->sym >= 0 && st->prog->syms[node->sym].mangled != NULL) {
        put_run(st, st->prog->syms[node->sym].mangled, st->prog->syms[node->sym].mangled_len);
        return;
    }
    put_run(st, node->str, node->str_len);
}

static void print_args(St *st, int32_t first) {
    put_char(st, '(');
    for (int32_t index = first; index >= 0; index = st->prog->nodes[index].next) {
        if (index != first) {
            put_char(st, ',');
        }
        print_sub(st, index, 2); /* each argument is an AssignmentExpression */
    }
    put_char(st, ')');
}

static void print_params(St *st, int32_t first) {
    put_char(st, '(');
    for (int32_t index = first; index >= 0; index = st->prog->nodes[index].next) {
        if (index != first) {
            put_char(st, ',');
        }
        print_sub(st, index, 2);
    }
    put_char(st, ')');
}

static void print_array(St *st, int32_t index) {
    const jm_node *node = &st->prog->nodes[index];
    put_char(st, '[');
    int first = 1;
    int last_hole = 0;
    for (int32_t elem = node->a; elem >= 0; elem = st->prog->nodes[elem].next) {
        if (!first) {
            put_char(st, ',');
        }
        first = 0;
        last_hole = st->prog->nodes[elem].kind == JN_HOLE;
        if (!last_hole) {
            print_sub(st, elem, 2);
        }
    }
    if (last_hole) {
        put_char(st, ','); /* a trailing elision needs its own comma to keep the length */
    }
    put_char(st, ']');
}

static void print_key(St *st, int32_t index, int computed) {
    if (computed) {
        put_char(st, '[');
        print_sub(st, index, 2);
        put_char(st, ']');
        return;
    }
    Py_ssize_t name_len;
    const Py_UCS4 *name = string_ident(&st->prog->nodes[index], &name_len);
    if (name != NULL && !is_proto_key(name, name_len)) {
        put_run(st, name, name_len); /* {"x":1} -> {x:1} */
        return;
    }
    print_expr(st, index);
}

static void print_object(St *st, int32_t index) {
    const jm_node *node = &st->prog->nodes[index];
    put_char(st, '{');
    int first = 1;
    for (int32_t prop = node->a; prop >= 0; prop = st->prog->nodes[prop].next) {
        if (!first) {
            put_char(st, ',');
        }
        first = 0;
        const jm_node *pn = &st->prog->nodes[prop];
        if (pn->kind == JN_SPREAD) {
            put_ascii(st, "...");
            print_sub(st, pn->a, 2);
            continue;
        }
        int computed = (pn->flags & JN_F_COMPUTED) != 0;
        if (pn->flags & JN_F_GET) {
            put_ascii(st, "get ");
        } else if (pn->flags & JN_F_SET) {
            put_ascii(st, "set ");
        }
        if (pn->flags & (JN_F_METHOD | JN_F_GET | JN_F_SET)) {
            const jm_node *fn = &st->prog->nodes[pn->b];
            if (fn->flags & JN_F_ASYNC) {
                put_ascii(st, "async ");
            }
            if (fn->flags & JN_F_GENERATOR) {
                put_char(st, '*');
            }
            print_key(st, pn->a, computed);
            print_function(st, pn->b, 1);
        } else if (pn->flags & JN_F_SHORTHAND) {
            /* {x} abbreviates {x:x} and {x=d} abbreviates {x:x=d}; once the bound name is
               renamed the property key and the local diverge, so expand back to key:value */
            const jm_node *id = &st->prog->nodes[pn->a]; /* a shorthand key is always an identifier */
            if (id->sym >= 0 && st->prog->syms[id->sym].mangled != NULL) {
                put_run(st, id->str, id->str_len);
                put_char(st, ':');
            }
            print_sub(st, pn->b >= 0 ? pn->b : pn->a, 2);
        } else {
            print_key(st, pn->a, computed);
            put_char(st, ':');
            print_sub(st, pn->b, 2);
        }
    }
    put_char(st, '}');
}

static void print_template(St *st, int32_t index) {
    const jm_node *node = &st->prog->nodes[index];
    for (int32_t part = node->a; part >= 0; part = st->prog->nodes[part].next) {
        const jm_node *pn = &st->prog->nodes[part];
        if (pn->kind == JN_QUASI) {
            print_text(st, pn);
        } else {
            print_expr(st, part); /* the substitution expression, no surrounding parens */
        }
    }
}

/* The leftmost token of an expression statement decides whether it must be wrapped:
   an object literal, a function or a class expression in statement position would be
   parsed as a block or a declaration. */
static int starts_with_brace_or_keyword(const jm_program *prog, int32_t index) {
    for (;;) {
        const jm_node *node = &prog->nodes[index];
        switch (node->kind) {
        case JN_OBJECT:
        case JN_FUNC:
        case JN_CLASS:
            return 1;
        case JN_BINARY:
        case JN_LOGICAL:
        case JN_ASSIGN:
        case JN_COND:
        case JN_SEQ:
            index = node->a; /* walk the left spine to the first emitted token */
            break;
        case JN_MEMBER_EXPR:
            /* `let[` in statement position parses as a let-declaration, so an
               expression statement `let[x]` (member access on a variable `let`)
               must be parenthesized. */
            if ((node->flags & JN_F_COMPUTED) && prog->nodes[node->a].kind == JN_IDENT &&
                prog->nodes[node->a].str_len == 3 && prog->nodes[node->a].str[0] == 'l' &&
                prog->nodes[node->a].str[1] == 'e' && prog->nodes[node->a].str[2] == 't') {
                return 1;
            }
            index = node->a;
            break;
        case JN_CALL:
        case JN_TAGGED:
            index = node->a;
            break;
        case JN_UPDATE:
            if (node->flags & JN_F_PREFIX) {
                return 0;
            }
            index = node->a;
            break;
        default:
            return 0;
        }
    }
}

/* Emit a numeric literal in its shortest value-preserving form: drop `_` separators,
   remove redundant zeros (1.0 -> 1, 0.5 -> .5, 5. -> 5), and fold trailing integer
   zeros into an exponent (1000 -> 1e3) when shorter. Radix (0x/0o/0b), legacy-octal,
   exponent-bearing and over-long literals are emitted underscore-free but unchanged. */
static void print_number(St *st, const jm_node *node) {
    Py_UCS4 buf[64];
    Py_ssize_t len = 0;
    int overflow = 0;
    for (Py_ssize_t index = 0; index < node->str_len; index++) {
        if (node->str[index] == '_') {
            continue;
        }
        if (len >= (Py_ssize_t)(sizeof(buf) / sizeof(buf[0]))) {
            overflow = 1;
            break;
        }
        buf[len++] = node->str[index];
    }
    if (overflow) { /* an unusually long literal (a huge exponent): one run, separators removed */
        Py_UCS4 *big = jm_malloc((size_t)node->str_len * sizeof(Py_UCS4));
        if (big == NULL) {                         /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            put_run(st, node->str, node->str_len); /* GCOVR_EXCL_LINE */
            return;                                /* GCOVR_EXCL_LINE */
        }
        Py_ssize_t pos = 0;
        for (Py_ssize_t index = 0; index < node->str_len; index++) {
            if (node->str[index] != '_') {
                big[pos++] = node->str[index];
            }
        }
        put_run(st, big, pos);
        jm_free(big);
        return;
    }
    int has_e = 0;
    Py_ssize_t dot = -1;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (buf[index] == 'e' || buf[index] == 'E') {
            has_e = 1;
        }
        if (buf[index] == '.') { /* a valid numeric literal has at most one dot */
            dot = index;
        }
    }
    int radix = len >= 2 && buf[0] == '0' &&
                (buf[1] == 'x' || buf[1] == 'X' || buf[1] == 'o' || buf[1] == 'O' || buf[1] == 'b' || buf[1] == 'B');
    /* a leading 0 followed by another digit (not x/o/b) is a legacy octal, left as-is */
    int legacy = dot < 0 && !has_e && len > 1 && buf[0] == '0' && buf[1] <= '9';
    if (radix || has_e || legacy) {
        put_run(st, buf, len); /* leave these forms alone beyond stripping separators */
        return;
    }
    Py_UCS4 out[80];
    Py_ssize_t out_len = 0;
    if (dot < 0) {
        Py_ssize_t zeros = 0;
        while (zeros < len && buf[len - 1 - zeros] == '0') {
            zeros++;
        }
        Py_ssize_t head = len - zeros;
        if (head == 0) { /* the literal 0 */
            put_run(st, buf, len);
            return;
        }
        char digits[8];
        int digit_len = snprintf(digits, sizeof(digits), "%lld", (long long)zeros);
        if (zeros >= 1 && head + 1 + digit_len < len) { /* <head>e<zeros> is shorter */
            for (Py_ssize_t index = 0; index < head; index++) {
                out[out_len++] = buf[index];
            }
            out[out_len++] = 'e';
            for (int index = 0; index < digit_len; index++) {
                out[out_len++] = (Py_UCS4)(unsigned char)digits[index];
            }
            put_run(st, out, out_len);
            return;
        }
        put_run(st, buf, len);
        return;
    }
    Py_ssize_t istart = 0;
    while (istart < dot && buf[istart] == '0') {
        istart++; /* 0.5 -> .5 */
    }
    Py_ssize_t fend = len;
    while (fend > dot + 1 && buf[fend - 1] == '0') {
        fend--; /* 1.50 -> 1.5 */
    }
    for (Py_ssize_t index = istart; index < dot; index++) {
        out[out_len++] = buf[index];
    }
    if (fend > dot + 1) {
        out[out_len++] = '.';
        for (Py_ssize_t index = dot + 1; index < fend; index++) {
            out[out_len++] = buf[index];
        }
    }
    if (out_len == 0) { /* 0.0 collapsed to nothing */
        out[out_len++] = '0';
    }
    put_run(st, out, out_len);
}

/* A BigInt literal carries no dot or exponent, so stripping numeric separators is its only safe
   minification (`1_000n` -> `1000n`); the value is unchanged. The common no-separator case emits
   the borrowed lexeme verbatim with no copy. */
static void print_bigint(St *st, const jm_node *node) {
    Py_ssize_t index = 0;
    while (index < node->str_len && node->str[index] != '_') {
        index++;
    }
    if (index == node->str_len) {
        print_text(st, node);
        return;
    }
    Py_UCS4 *buf = jm_malloc((size_t)node->str_len * sizeof(Py_UCS4));
    if (buf == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation-failure path keeps the literal verbatim */
        print_text(st, node); /* GCOVR_EXCL_LINE */
        return;               /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t len = 0;
    for (Py_ssize_t scan = 0; scan < node->str_len; scan++) {
        if (node->str[scan] != '_') {
            buf[len++] = node->str[scan];
        }
    }
    put_run(st, buf, len);
    jm_free(buf);
}

static void print_expr(St *st, int32_t index) {
    const jm_node *node = &st->prog->nodes[index];
    switch (node->kind) { /* GCOVR_EXCL_BR_LINE: exhaustive switch; default unreachable */
    case JN_NUM:
        print_number(st, node);
        break;
    case JN_BIGINT:
        print_bigint(st, node);
        break;
    case JN_IDENT:
    case JN_STRING:
        print_text(st, node);
        break;
    case JN_REGEX:
        print_text(st, node);
        st->after_regex = 1;
        break;
    case JN_TEMPLATE:
        print_template(st, index);
        break;
    case JN_TAGGED:
        /* an optional chain may not be a template tag (§13.3.1), so its load-bearing parens stay */
        if (st->prog->nodes[node->a].flags & JN_F_PAREN) {
            put_char(st, '(');
            print_expr(st, node->a);
            put_char(st, ')');
        } else {
            print_sub(st, node->a, 18);
        }
        print_template(st, node->b);
        break;
    case JN_ARRAY:
        print_array(st, index);
        break;
    case JN_OBJECT:
        print_object(st, index);
        break;
    case JN_SPREAD:
        put_ascii(st, "...");
        print_sub(st, node->a, 2);
        break;
    case JN_SEQ:
        for (int32_t expr = node->a; expr >= 0; expr = st->prog->nodes[expr].next) {
            if (expr != node->a) {
                put_char(st, ',');
            }
            print_sub(st, expr, 2);
        }
        break;
    case JN_ASSIGN:
        print_sub(st, node->a, 3); /* the target binds tighter than assignment */
        put_ascii(st, assign_op_str(node->op));
        print_sub(st, node->b, 2);
        break;
    case JN_COND:
        print_sub(st, node->a, 4);
        put_char(st, '?');
        print_sub(st, node->b, 2);
        put_char(st, ':');
        print_sub(st, node->c, 2);
        break;
    case JN_BINARY:
    case JN_LOGICAL: {
        if (node->op == JT_IDENT && st->no_in) {
            const jm_node *word = &st->prog->nodes[node->c];
            if (word->str_len == 2) { /* `in` is the only two-character JT_IDENT operator */
                put_char(st, '(');
                int saved = st->no_in;
                st->no_in = 0;
                print_expr(st, index);
                st->no_in = saved;
                put_char(st, ')');
                break;
            }
        }
        int prec = op_prec(node->op);
        int pow = node->op == JT_POW;
        /* ** is right-associative, so the same-precedence operand sits on the right (printed at
           prec, the left at prec+1). The grammar also forbids an unparenthesized UnaryExpression
           on the left of ** -- `-2**2` is a SyntaxError -- so the left prints at UpdateExpression
           precedence (17) to parenthesize a unary/await/prefix-update left operand. */
        print_mix_operand(st, node->op, node->a, pow ? 17 : prec);
        if (node->op == JT_IDENT) {
            print_text(st, &st->prog->nodes[node->c]); /* in / instanceof, spaced by the guard */
        } else {
            put_ascii(st, binary_op_str(node->op));
        }
        print_mix_operand(st, node->op, node->b, pow ? prec : prec + 1);
        break;
    }
    case JN_UNARY:
        if (node->str != NULL) {
            print_text(st, node); /* typeof / void / delete - the guard spaces the operand */
        } else {
            put_ascii(st, node->op == JT_NOT ? "!" : node->op == JT_BIT_NOT ? "~" : node->op == JT_PLUS ? "+" : "-");
        }
        print_sub(st, node->a, 16);
        break;
    case JN_AWAIT:
        put_ascii(st, "await ");
        print_sub(st, node->a, 16);
        break;
    case JN_YIELD:
        put_ascii(st, "yield");
        if (node->flags & JN_F_DELEGATE) {
            put_char(st, '*');
        }
        if (node->a >= 0) {
            print_sub(st, node->a, 2);
        }
        break;
    case JN_UPDATE:
        if (node->flags & JN_F_PREFIX) {
            put_ascii(st, node->op == JT_INC ? "++" : "--");
            print_sub(st, node->a, 16);
        } else {
            print_sub(st, node->a, 17);
            put_ascii(st, node->op == JT_INC ? "++" : "--");
        }
        break;
    case JN_MEMBER_EXPR: {
        /* whether a `a["x"]` computed access lowers to `a.x`: only when the key spells an
           IdentifierName. Decide before printing the object, because a dot after a numeric
           object needs the object parenthesized just like a plain dot access does. */
        Py_ssize_t name_len;
        const Py_UCS4 *name = (node->flags & JN_F_COMPUTED) ? string_ident(&st->prog->nodes[node->b], &name_len) : NULL;
        int prints_dot = !(node->flags & JN_F_COMPUTED) || name != NULL;
        /* a `.` after a numeric literal would re-lex as part of the number (`1.toString` -> `1.`
           then `toString`, `25["x"]` -> the invalid `25.x`), so parenthesize the number; an
           optional chain with load-bearing parens keeps them. */
        if ((prints_dot && st->prog->nodes[node->a].kind == JN_NUM) || (st->prog->nodes[node->a].flags & JN_F_PAREN)) {
            put_char(st, '(');
            print_expr(st, node->a);
            put_char(st, ')');
        } else {
            print_sub(st, node->a, 18);
        }
        if (name != NULL) {
            put_ascii(st, (node->flags & JN_F_OPTIONAL) ? "?." : ".");
            put_run(st, name, name_len); /* a["x"] -> a.x */
        } else if (node->flags & JN_F_COMPUTED) {
            if (node->flags & JN_F_OPTIONAL) {
                put_ascii(st, "?.");
            }
            put_char(st, '[');
            print_expr(st, node->b);
            put_char(st, ']');
        } else {
            put_ascii(st, (node->flags & JN_F_OPTIONAL) ? "?." : ".");
            print_text(st, &st->prog->nodes[node->b]);
        }
        break;
    }
    case JN_CALL:
        if (st->prog->nodes[node->a].flags & JN_F_PAREN) {
            put_char(st, '(');
            print_expr(st, node->a);
            put_char(st, ')');
        } else {
            print_sub(st, node->a, 18);
        }
        if (node->flags & JN_F_OPTIONAL) {
            put_ascii(st, "?.");
        }
        print_args(st, node->b);
        break;
    case JN_NEW:
        put_ascii(st, "new ");
        /* an optional chain may not be a `new` callee (§13.3.5), so keep its load-bearing parens */
        if (new_callee_needs_parens(st->prog, node->a) || (st->prog->nodes[node->a].flags & JN_F_PAREN)) {
            put_char(st, '(');
            print_expr(st, node->a);
            put_char(st, ')');
        } else {
            print_sub(st, node->a, 18);
        }
        print_args(st, node->b);
        break;
    case JN_ARROW: {
        if (node->flags & JN_F_ASYNC) {
            put_ascii(st, "async ");
        }
        int32_t param = node->a;
        int single = param >= 0 && st->prog->nodes[param].kind == JN_IDENT && st->prog->nodes[param].next < 0;
        if (single) {
            print_text(st, &st->prog->nodes[param]);
        } else {
            print_params(st, param);
        }
        put_ascii(st, "=>");
        if (node->flags & JN_F_EXPRBODY) {
            if (starts_with_brace_or_keyword(st->prog, node->b)) {
                put_char(st, '(');
                print_expr(st, node->b);
                put_char(st, ')');
            } else {
                print_sub(st, node->b, 2);
            }
        } else {
            print_block(st, node->b);
        }
        break;
    }
    case JN_FUNC:
        print_function(st, index, 0);
        break;
    case JN_CLASS:
        print_stmt(st, index); /* a class expression prints like its declaration form */
        break;
    default:   /* GCOVR_EXCL_LINE: every expression kind is handled above */
        break; /* GCOVR_EXCL_LINE */
    }
}

static void print_function(St *st, int32_t index, int as_method) {
    const jm_node *node = &st->prog->nodes[index];
    if (!as_method) {
        if (node->flags & JN_F_ASYNC) {
            put_ascii(st, "async ");
        }
        put_ascii(st, "function");
        if (node->flags & JN_F_GENERATOR) {
            put_char(st, '*');
        }
        if (node->str != NULL) {
            print_text(st, node); /* the guard spaces a name after `function`, not after `*` */
        }
    }
    print_params(st, node->a);
    print_block(st, node->b);
}

static void print_class(St *st, int32_t index) {
    const jm_node *node = &st->prog->nodes[index];
    put_ascii(st, "class");
    if (node->str != NULL) {
        put_char(st, ' ');
        print_text(st, node);
    }
    if (node->a >= 0) {
        put_ascii(st, " extends ");
        print_sub(st, node->a, 18);
    }
    put_char(st, '{');
    for (int32_t member = node->b; member >= 0; member = st->prog->nodes[member].next) {
        const jm_node *mn = &st->prog->nodes[member];
        if (mn->flags & JN_F_STATIC) {
            put_ascii(st, "static ");
        }
        int computed = (mn->flags & JN_F_COMPUTED) != 0;
        if (mn->decl == 3) { /* a field */
            print_key(st, mn->a, computed);
            if (mn->b >= 0) {
                put_char(st, '=');
                print_sub(st, mn->b, 2);
            }
            if (st->prog->nodes[member].next >= 0) {
                put_char(st, ';'); /* separate fields; the printer drops the trailing one */
            }
        } else {
            const jm_node *fn = &st->prog->nodes[mn->b];
            if (fn->flags & JN_F_ASYNC) {
                put_ascii(st, "async ");
            }
            if (fn->flags & JN_F_GENERATOR) {
                put_char(st, '*');
            }
            if (mn->decl == 1) {
                put_ascii(st, "get ");
            } else if (mn->decl == 2) {
                put_ascii(st, "set ");
            }
            print_key(st, mn->a, computed);
            print_function(st, mn->b, 1);
        }
    }
    put_char(st, '}');
}

static void print_var(St *st, int32_t index) {
    const jm_node *node = &st->prog->nodes[index];
    put_ascii(st, node->decl == 0 ? "var" : node->decl == 1 ? "let" : "const");
    put_char(st, ' ');
    for (int32_t declr = node->a; declr >= 0; declr = st->prog->nodes[declr].next) {
        if (declr != node->a) {
            put_char(st, ',');
        }
        const jm_node *dn = &st->prog->nodes[declr];
        print_sub(st, dn->a, 2);
        if (dn->b >= 0) {
            put_char(st, '=');
            print_sub(st, dn->b, 2);
        }
    }
}

static void print_block(St *st, int32_t index) {
    const jm_node *node = &st->prog->nodes[index];
    put_char(st, '{');
    int pending = 0;
    for (int32_t stmt = node->a; stmt >= 0; stmt = st->prog->nodes[stmt].next) {
        if (st->prog->nodes[stmt].kind == JN_EMPTY) {
            continue; /* drop empty statements inside a block */
        }
        if (pending) {
            put_char(st, ';');
        }
        pending = print_stmt(st, stmt);
    }
    put_char(st, '}');
}

/* A substatement (an if/for/while/with body, or an if branch): an empty one is a
   bare `;`, a block prints itself; anything else is printed and its need for a
   terminator is returned to the caller. */
static int print_substmt(St *st, int32_t index) {
    if (st->prog->nodes[index].kind == JN_EMPTY) {
        put_char(st, ';');
        return 0;
    }
    return print_stmt(st, index);
}

/* A block scopes its `let`/`const`/class/function declarations, so those keep their braces; a `var` or
   an ordinary statement does not, so a single one can shed the braces. */
static int keeps_block_scope(const jm_program *prog, int32_t stmt) {
    int kind = prog->nodes[stmt].kind;
    return kind == JN_CLASS || kind == JN_FUNC || (kind == JN_VAR && prog->nodes[stmt].decl != 0);
}

/* The statement a block prints braceless -- its single scope-free statement -- or -1 when the
   braces stay (several statements, or one that needs the block scope). */
static int32_t block_single_stmt(const St *st, int32_t index) {
    int32_t only = -1;
    int count = 0;
    for (int32_t stmt = st->prog->nodes[index].a; stmt >= 0; stmt = st->prog->nodes[stmt].next) {
        if (st->prog->nodes[stmt].kind == JN_EMPTY) {
            continue;
        }
        only = stmt;
        if (++count > 1) {
            break;
        }
    }
    return count == 1 && !keeps_block_scope(st->prog, only) ? only : -1;
}

/* Whether the statement, printed braceless, ends in an `if` with no `else` -- the shape a following
   `else` would re-attach to (the grammar binds `else` to the nearest open `if`). The walk descends
   every trailing substatement position exactly as print_branch flattens it: a block that keeps its
   braces closes the chain. */
static int ends_with_open_if(const St *st, int32_t index) {
    for (;;) {
        const jm_node *node = &st->prog->nodes[index];
        switch (node->kind) {
        case JN_BLOCK:
            index = block_single_stmt(st, index);
            if (index < 0) {
                return 0;
            }
            break;
        case JN_IF:
            if (node->c < 0) {
                return 1;
            }
            index = node->c;
            break;
        case JN_FOR:
            index = node->d;
            break;
        case JN_FORIN:
        case JN_FOROF:
            index = node->c;
            break;
        case JN_WHILE:
        case JN_WITH:
            index = node->b;
            break;
        case JN_LABEL:
            index = node->a;
            break;
        default: /* a do-while, switch, try or plain statement ends closed */
            return 0;
        }
    }
}

/* A substatement -- a loop/label/with body or an if branch -- prints without braces when it is a
   block holding a single scope-free statement: `for(;;){g()}` -> `for(;;)g()`. An if consequent
   (has_else set) that would end in an open `if` -- which the following `else` would re-attach to --
   is braced instead, whether it arrived as a block or as a bare statement whose trailing
   substatement flattens into that shape (`if(a)b:{if(c)break b}else...`). */
static int print_branch(St *st, int32_t index, int has_else) {
    if (has_else && ends_with_open_if(st, index)) {
        if (st->prog->nodes[index].kind == JN_BLOCK) {
            print_substmt(st, index);
        } else {
            put_char(st, '{');
            print_stmt(st, index);
            put_char(st, '}');
        }
        return 0;
    }
    if (st->prog->nodes[index].kind == JN_BLOCK) {
        int32_t only = block_single_stmt(st, index);
        if (only >= 0) {
            return print_stmt(st, only);
        }
    }
    return print_substmt(st, index);
}

/* Print a statement; return 1 if a separating `;` is needed before whatever follows. */
static int print_stmt(St *st, int32_t index) {
    const jm_node *node = &st->prog->nodes[index];
    switch (node->kind) { /* GCOVR_EXCL_BR_LINE: exhaustive switch; default unreachable */
    case JN_BLOCK:
        print_block(st, index);
        return 0;
    case JN_EXPR_STMT:
        if (starts_with_brace_or_keyword(st->prog, node->a)) {
            put_char(st, '(');
            print_expr(st, node->a);
            put_char(st, ')');
        } else {
            print_expr(st, node->a);
        }
        return 1;
    case JN_VAR:
        print_var(st, index);
        return 1;
    case JN_IF: {
        put_ascii(st, "if(");
        print_expr(st, node->a);
        put_char(st, ')');
        if (node->c >= 0) {
            int then_semi = print_branch(st, node->b, 1);
            if (then_semi) {
                put_char(st, ';');
            }
            put_ascii(st, "else"); /* the adjacency guard adds a space only before a word */
            return print_branch(st, node->c, 0);
        }
        return print_branch(st, node->b, 0);
    }
    case JN_FOR:
        put_ascii(st, "for(");
        if (node->a >= 0) {
            st->no_in = 1; /* parenthesize a top-level `in` in the init clause */
            if (st->prog->nodes[node->a].kind == JN_VAR) {
                print_var(st, node->a);
            } else {
                print_expr(st, node->a);
            }
            st->no_in = 0;
        }
        put_char(st, ';');
        if (node->b >= 0) {
            print_expr(st, node->b);
        }
        put_char(st, ';');
        if (node->c >= 0) {
            print_expr(st, node->c);
        }
        put_char(st, ')');
        return print_branch(st, node->d, 0);
    case JN_FORIN:
    case JN_FOROF: {
        put_ascii(st, "for");
        if (node->flags & JN_F_AWAIT) {
            put_ascii(st, " await");
        }
        put_char(st, '(');
        if (st->prog->nodes[node->a].kind == JN_VAR) {
            print_var(st, node->a);
        } else {
            print_sub(st, node->a, 17);
        }
        put_ascii(st, node->kind == JN_FOROF ? " of " : " in ");
        print_sub(st, node->b, node->kind == JN_FOROF ? 2 : 1);
        put_char(st, ')');
        return print_branch(st, node->c, 0);
    }
    case JN_WHILE:
        put_ascii(st, "while(");
        print_expr(st, node->a);
        put_char(st, ')');
        return print_branch(st, node->b, 0);
    case JN_DOWHILE:
        put_ascii(st, "do"); /* the adjacency guard spaces `do` from a word body */
        if (print_branch(st, node->a, 0)) {
            put_char(st, ';');
        }
        put_ascii(st, "while(");
        print_expr(st, node->b);
        put_char(st, ')');
        return 1;
    case JN_SWITCH:
        put_ascii(st, "switch(");
        print_expr(st, node->a);
        put_ascii(st, "){");
        /* one pending semicolon spans the whole body so a clause's last statement is
           still separated from the next case/default label. */
        int pending = 0;
        for (int32_t clause = node->b; clause >= 0; clause = st->prog->nodes[clause].next) {
            const jm_node *cn = &st->prog->nodes[clause];
            if (pending) {
                put_char(st, ';');
                pending = 0;
            }
            if (cn->a >= 0) {
                put_ascii(st, "case ");
                print_expr(st, cn->a);
                put_char(st, ':');
            } else {
                put_ascii(st, "default:");
            }
            for (int32_t stmt = cn->b; stmt >= 0; stmt = st->prog->nodes[stmt].next) {
                if (st->prog->nodes[stmt].kind == JN_EMPTY) {
                    continue;
                }
                if (pending) {
                    put_char(st, ';');
                }
                pending = print_stmt(st, stmt);
            }
        }
        put_char(st, '}');
        return 0;
    case JN_TRY:
        put_ascii(st, "try");
        print_block(st, node->a);
        if (node->c >= 0) {
            put_ascii(st, "catch");
            if (node->b >= 0) {
                put_char(st, '(');
                print_sub(st, node->b, 2);
                put_char(st, ')');
            }
            print_block(st, node->c);
        }
        if (node->d >= 0) {
            put_ascii(st, "finally");
            print_block(st, node->d);
        }
        return 0;
    case JN_RETURN:
        put_ascii(st, "return"); /* the guard spaces a word argument, not `-1`/`.5`/`/re/` */
        if (node->a >= 0) {
            print_expr(st, node->a);
        }
        return 1;
    case JN_THROW:
        put_ascii(st, "throw");
        print_expr(st, node->a);
        return 1;
    case JN_BREAK:
    case JN_CONTINUE:
        put_ascii(st, node->kind == JN_BREAK ? "break" : "continue");
        if (node->str != NULL) {
            put_char(st, ' ');
            print_text(st, node);
        }
        return 1;
    case JN_FUNC:
        print_function(st, index, 0);
        return 0;
    case JN_CLASS:
        print_class(st, index);
        return 0;
    case JN_LABEL:
        print_text(st, node);
        put_char(st, ':');
        return print_branch(st, node->a, 0);
    case JN_WITH:
        put_ascii(st, "with(");
        print_expr(st, node->a);
        put_char(st, ')');
        return print_branch(st, node->b, 0);
    case JN_DEBUGGER:
        put_ascii(st, "debugger");
        return 1;
    default:      /* GCOVR_EXCL_LINE: every statement kind is handled above */
        return 0; /* GCOVR_EXCL_LINE */
    }
}

Py_UCS4 *jm_print(const jm_program *prog, Py_ssize_t *out_len) {
    St st = {.prog = prog, .data = NULL, .len = 0, .cap = 0, .last = 0, .failed = 0};
    /* Reserve up front so a program that minifies to nothing (only empty statements,
       whitespace or comments) still returns a real, non-NULL buffer - a NULL return
       is reserved for an actual allocation failure. */
    grow(&st, 1);
    if (st.failed) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return NULL; /* GCOVR_EXCL_LINE */
    }
    /* Kept license/banner comments lead the output in source order. They carry no semantics, so hoisting
       them to the top keeps a license header first and visible and stays byte-exact and idempotent,
       without threading source offsets through the fold/compress/mangle passes. put_run's adjacency guard
       spaces the comment's closing delimiter from a following token where a merge would change the run. */
    for (int32_t index = 0; index < prog->comment_count; index++) {
        put_run(&st, prog->comments[index].text, prog->comments[index].len);
    }
    const jm_node *root = &prog->nodes[prog->root];
    int pending = 0;
    for (int32_t stmt = root->a; stmt >= 0; stmt = prog->nodes[stmt].next) {
        if (prog->nodes[stmt].kind == JN_EMPTY) {
            continue;
        }
        if (pending) {
            put_char(&st, ';');
        }
        pending = print_stmt(&st, stmt);
    }
    if (st.failed) {      /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        jm_free(st.data); /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    *out_len = st.len;
    return st.data;
}
