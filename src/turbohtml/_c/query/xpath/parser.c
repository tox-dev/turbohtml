/* Parses the token stream into the immutable arena AST a compiled program holds, then
   collapses the `//X` abbreviation into a single descendant step where it is safe. */

#include "core/common.h"
#include "query/xpath/internal.h"
#include "query/xpath/xpath.h"

#include <string.h>

typedef struct {
    lexer lx;
    xp_program *prog;
    int failed;
    int depth; /* current parse recursion depth, capped at XP_MAX_DEPTH */
    const char *msg;
    Py_ssize_t err_pos;     /* offset of the offending token when failed */
    const Py_UCS4 *err_tok; /* the offending token text (into the source) */
    Py_ssize_t err_tok_len;
} parser;

static void fail(parser *ps, const char *msg) {
    if (!ps->failed) {
        ps->failed = 1;
        ps->msg = msg;
        ps->err_pos = ps->lx.tokpos;
        ps->err_tok = ps->lx.src + ps->lx.tokpos;
        ps->err_tok_len = ps->lx.pos - ps->lx.tokpos;
    }
}

static int32_t parse_expr(parser *ps);
static int32_t parse_union(parser *ps);

static int accept(parser *ps, tok_kind kind) {
    /* a lexer error always leaves kind == TK_EOF, and accept is never called with
       TK_EOF, so kind == the (non-EOF) target already implies no pending error */
    if (ps->lx.kind == kind) {
        lex_next(&ps->lx);
        if (ps->lx.error) {
            fail(ps, "invalid character in expression");
        }
        return 1;
    }
    return 0;
}

static void expect(parser *ps, tok_kind kind, const char *msg) {
    if (!accept(ps, kind)) {
        fail(ps, msg);
    }
}

/* Copy the lexer's current NAME/LITERAL text into the node's owned string. */
static int copy_text(xp_program *prog, int32_t idx, const Py_UCS4 *src, Py_ssize_t len) {
    Py_UCS4 *buf = PyMem_Malloc((size_t)len * sizeof(Py_UCS4));
    if (buf == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;     /* GCOVR_EXCL_LINE */
    }
    memcpy(buf, src, (size_t)len * sizeof(Py_UCS4));
    prog->nodes[idx].str = buf;
    prog->nodes[idx].str_len = len;
    return 0;
}

/* A NodeTest after an axis has been chosen; fills test/str on the step node. */
static void parse_node_test(parser *ps, int32_t step) {
    lexer *lx = &ps->lx;
    if (lx->kind == TK_STAR) {
        ps->prog->nodes[step].test = NT_STAR;
        lex_next(lx);
        return;
    }
    if (lx->kind != TK_NAME) {
        fail(ps, "expected a node test");
        return;
    }
    /* node()/text()/comment()/processing-instruction() when followed by '(' */
    int is_node = xp_name_eq(lx, "node");
    int is_text = xp_name_eq(lx, "text");
    int is_comment = xp_name_eq(lx, "comment");
    int is_pi = xp_name_eq(lx, "processing-instruction");
    Py_ssize_t save = lx->pos;
    while (save < lx->len && xp_is_space(lx->src[save])) {
        save++;
    }
    int kind_test = (is_node || is_text || is_comment || is_pi) && save < lx->len && lx->src[save] == '(';
    if (kind_test) {
        ps->prog->nodes[step].test = (uint8_t)(is_node ? NT_NODE : is_text ? NT_TEXT : is_comment ? NT_COMMENT : NT_PI);
        lex_next(lx); /* the name */
        expect(ps, TK_LPAREN, "expected '('");
        if (is_pi && lx->kind == TK_LITERAL) {
            if (copy_text(ps->prog, step, lx->tstart, lx->tlen) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                fail(ps, "out of memory");                             /* GCOVR_EXCL_LINE */
            } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
            lex_next(lx);
        }
        expect(ps, TK_RPAREN, "expected ')'");
        return;
    }
    ps->prog->nodes[step].test = NT_NAME;
    ps->prog->nodes[step].prefix_len = lx->tprefix;            /* the resolved URI binds at eval time */
    if (copy_text(ps->prog, step, lx->tstart, lx->tlen) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        fail(ps, "out of memory");                             /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
    lex_next(lx);
}

/* Parse the predicate list onto a step (or filter), returning the head index. */
static int32_t parse_predicates(parser *ps) {
    int32_t head = -1;
    int32_t tail = -1;
    while (ps->lx.kind == TK_LBRACK) {
        lex_next(&ps->lx);
        int32_t pred = xn_new(ps->prog, XN_PRED);
        if (pred < 0) {                /* GCOVR_EXCL_BR_LINE: alloc */
            fail(ps, "out of memory"); /* GCOVR_EXCL_LINE */
            return head;               /* GCOVR_EXCL_LINE */
        }
        int32_t expr = parse_expr(ps);
        ps->prog->nodes[pred].first = expr;
        expect(ps, TK_RBRACK, "expected ']'");
        if (head < 0) {
            head = pred;
        } else {
            ps->prog->nodes[tail].next = pred;
        }
        tail = pred;
    }
    return head;
}

static int axis_from_name(const lexer *lx, enum xp_axis *out) {
    static const struct {
        const char *name;
        enum xp_axis axis;
    } table[] = {
        {"child", AX_CHILD},
        {"descendant", AX_DESCENDANT},
        {"descendant-or-self", AX_DESCENDANT_OR_SELF},
        {"attribute", AX_ATTRIBUTE},
        {"self", AX_SELF},
        {"parent", AX_PARENT},
        {"ancestor", AX_ANCESTOR},
        {"ancestor-or-self", AX_ANCESTOR_OR_SELF},
        {"following-sibling", AX_FOLLOWING_SIBLING},
        {"preceding-sibling", AX_PRECEDING_SIBLING},
        {"following", AX_FOLLOWING},
        {"preceding", AX_PRECEDING},
        {"namespace", AX_NAMESPACE},
    };
    for (size_t index = 0; index < sizeof(table) / sizeof(table[0]); index++) {
        if (xp_name_eq(lx, table[index].name)) {
            *out = table[index].axis;
            return 1;
        }
    }
    return 0;
}

/* One Step: handles ., .., @abbrev, axis::, and a bare NodeTest, plus predicates. */
static int32_t parse_step(parser *ps) {
    lexer *lx = &ps->lx;
    if (lx->kind == TK_DOT) {
        lex_next(lx);
        int32_t step = xn_new(ps->prog, XN_STEP);
        if (step < 0) { /* GCOVR_EXCL_BR_LINE: arena allocation failure cannot be forced */
            return -1;  /* GCOVR_EXCL_LINE */
        }
        ps->prog->nodes[step].axis = AX_SELF;
        ps->prog->nodes[step].test = NT_NODE;
        return step;
    }
    if (lx->kind == TK_DOTDOT) {
        lex_next(lx);
        int32_t step = xn_new(ps->prog, XN_STEP);
        if (step < 0) { /* GCOVR_EXCL_BR_LINE: arena allocation failure cannot be forced */
            return -1;  /* GCOVR_EXCL_LINE */
        }
        ps->prog->nodes[step].axis = AX_PARENT;
        ps->prog->nodes[step].test = NT_NODE;
        return step;
    }
    int32_t step = xn_new(ps->prog, XN_STEP);
    if (step < 0) {                /* GCOVR_EXCL_BR_LINE: alloc */
        fail(ps, "out of memory"); /* GCOVR_EXCL_LINE */
        return -1;                 /* GCOVR_EXCL_LINE */
    }
    enum xp_axis axis = AX_CHILD;
    if (lx->kind == TK_AT) {
        lex_next(lx);
        axis = AX_ATTRIBUTE;
    } else if (lx->kind == TK_NAME) {
        /* an AxisName immediately followed by '::' */
        Py_ssize_t save = lx->pos;
        while (save + 1 < lx->len && xp_is_space(lx->src[save])) {
            save++;
        }
        enum xp_axis named;
        if (save + 1 < lx->len && lx->src[save] == ':' && lx->src[save + 1] == ':' && axis_from_name(lx, &named)) {
            axis = named;
            lex_next(lx); /* the axis name */
            expect(ps, TK_COLONCOLON, "expected '::'");
        }
    }
    ps->prog->nodes[step].axis = (uint8_t)axis;
    parse_node_test(ps, step);
    /* Resolve the predicate list before the store: parse_predicates calls xn_new,
       which may reallocate the arena, so computing &nodes[step].first up front would
       leave the assignment writing through a freed pointer. */
    int32_t predicates = parse_predicates(ps);
    ps->prog->nodes[step].first = predicates;
    return step;
}

static int starts_step(tok_kind kind) {
    return kind == TK_NAME || kind == TK_STAR || kind == TK_AT || kind == TK_DOT || kind == TK_DOTDOT;
}

/* A RelativeLocationPath chained onto an existing head step list (after / or //).
   Appends to *tail; inserts a synthetic descendant-or-self::node() for //. */
static void parse_relative_tail(parser *ps, int32_t *head, int32_t *tail, int dslash) {
    if (dslash) {
        int32_t ds = xn_new(ps->prog, XN_STEP);
        if (ds < 0) { /* GCOVR_EXCL_BR_LINE: arena allocation failure cannot be forced */
            return;   /* GCOVR_EXCL_LINE */
        }
        ps->prog->nodes[ds].axis = AX_DESCENDANT_OR_SELF;
        ps->prog->nodes[ds].test = NT_NODE;
        if (*head < 0) {
            *head = ds;
        } else {
            ps->prog->nodes[*tail].next = ds;
        }
        *tail = ds;
    }
    int32_t step = parse_step(ps);
    if (step < 0) { /* GCOVR_EXCL_BR_LINE: parse_step only returns negative on arena allocation failure */
        return;     /* GCOVR_EXCL_LINE */
    }
    if (*head < 0) {
        *head = step;
    } else {
        ps->prog->nodes[*tail].next = step;
    }
    *tail = step;
}

/* LocationPath / PathExpr without a leading FilterExpr. */
static int32_t parse_location_path(parser *ps) {
    int32_t path = xn_new(ps->prog, XN_PATH);
    if (path < 0) {                /* GCOVR_EXCL_BR_LINE: alloc */
        fail(ps, "out of memory"); /* GCOVR_EXCL_LINE */
        return -1;                 /* GCOVR_EXCL_LINE */
    }
    int32_t head = -1;
    int32_t tail = -1;
    if (ps->lx.kind == TK_SLASH) {
        ps->prog->nodes[path].absolute = 1;
        lex_next(&ps->lx);
        if (starts_step(ps->lx.kind)) {
            parse_relative_tail(ps, &head, &tail, 0);
        }
    } else if (ps->lx.kind == TK_DSLASH) {
        ps->prog->nodes[path].absolute = 1;
        lex_next(&ps->lx);
        parse_relative_tail(ps, &head, &tail, 1);
    } else {
        parse_relative_tail(ps, &head, &tail, 0);
    }
    while ((ps->lx.kind == TK_SLASH || ps->lx.kind == TK_DSLASH)) {
        int dslash = ps->lx.kind == TK_DSLASH;
        lex_next(&ps->lx);
        parse_relative_tail(ps, &head, &tail, dslash);
    }
    ps->prog->nodes[path].first = head;
    ps->prog->nodes[path].second = -1;
    return path;
}

/* PrimaryExpr: literal, number, ( expr ), function call. */
static int32_t parse_primary(parser *ps) {
    lexer *lx = &ps->lx;
    if (lx->kind == TK_LPAREN) {
        lex_next(lx);
        int32_t inner = parse_expr(ps);
        expect(ps, TK_RPAREN, "expected ')'");
        return inner;
    }
    if (lx->kind == TK_LITERAL) {
        int32_t lit = xn_new(ps->prog, XN_LIT);
        if (lit < 0) { /* GCOVR_EXCL_BR_LINE: arena allocation failure cannot be forced */
            return -1; /* GCOVR_EXCL_LINE */
        }
        if (copy_text(ps->prog, lit, lx->tstart, lx->tlen) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            fail(ps, "out of memory");                            /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
        lex_next(lx);
        return lit;
    }
    if (lx->kind == TK_NUM) {
        int32_t num = xn_new(ps->prog, XN_NUM);
        if (num < 0) { /* GCOVR_EXCL_BR_LINE: arena allocation failure cannot be forced */
            return -1; /* GCOVR_EXCL_LINE */
        }
        ps->prog->nodes[num].num = lx->num;
        lex_next(lx);
        return num;
    }
    if (lx->kind == TK_DOLLAR) {
        lex_next(lx); /* the variable name */
        if (lx->kind != TK_NAME) {
            fail(ps, "expected a name after '$'");
            return -1;
        }
        int32_t var = xn_new(ps->prog, XN_VAR);
        if (var < 0) { /* GCOVR_EXCL_BR_LINE: arena allocation failure cannot be forced */
            return -1; /* GCOVR_EXCL_LINE */
        }
        if (copy_text(ps->prog, var, lx->tstart, lx->tlen) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            fail(ps, "out of memory");                            /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
        lex_next(lx);
        return var;
    }
    /* The only remaining primary is a FunctionCall: starts_filter() guarantees a
       NAME here once '(', a literal, and a number have been ruled out above. */
    int32_t fn = xn_new(ps->prog, XN_FUNC);
    if (fn < 0) {                  /* GCOVR_EXCL_BR_LINE: alloc */
        fail(ps, "out of memory"); /* GCOVR_EXCL_LINE */
        return -1;                 /* GCOVR_EXCL_LINE */
    }
    if (copy_text(ps->prog, fn, lx->tstart, lx->tlen) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        fail(ps, "out of memory");                           /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
    lex_next(lx);
    expect(ps, TK_LPAREN, "expected '(' after function name");
    int32_t arg_head = -1;
    int32_t arg_tail = -1;
    if (lx->kind != TK_RPAREN) {
        for (;;) {
            int32_t arg = parse_expr(ps);
            if (arg_head < 0) {
                arg_head = arg;
            } else {
                ps->prog->nodes[arg_tail].next = arg;
            }
            arg_tail = arg;
            if (!accept(ps, TK_COMMA)) {
                break;
            }
        }
    }
    expect(ps, TK_RPAREN, "expected ')'");
    ps->prog->nodes[fn].first = arg_head;
    return fn;
}

/* A FilterExpr begins with a literal, number, '(', or a function call (a NAME
   immediately before '(' that is neither an axis name nor a kind test). Anything
   else begins a LocationPath. */
static int starts_filter(parser *ps) {
    tok_kind kind = ps->lx.kind;
    if (kind == TK_LITERAL || kind == TK_NUM || kind == TK_LPAREN || kind == TK_DOLLAR) {
        return 1;
    }
    if (kind != TK_NAME) {
        return 0;
    }
    Py_ssize_t save = ps->lx.pos;
    while (save < ps->lx.len && xp_is_space(ps->lx.src[save])) {
        save++;
    }
    /* a name followed by '(' cannot also be followed by '::', so paren already
       excludes an axis; only the kind tests need ruling out */
    int paren = save < ps->lx.len && ps->lx.src[save] == '(';
    int kind_test = xp_name_eq(&ps->lx, "node") || xp_name_eq(&ps->lx, "text") || xp_name_eq(&ps->lx, "comment") ||
                    xp_name_eq(&ps->lx, "processing-instruction");
    return paren && !kind_test;
}

/* FilterExpr: PrimaryExpr Predicate*, optionally continuing a PathExpr with /steps.
   Filter predicates apply to the whole node-set (so `(//a)[1]` differs from `//a[1]`)
   and are kept on an XN_FILTER node, distinct from a step's per-context predicates. */
static int32_t parse_filter_or_path(parser *ps) {
    if (!starts_filter(ps)) {
        return parse_location_path(ps);
    }
    int32_t primary = parse_primary(ps);
    int32_t preds = parse_predicates(ps);
    int32_t base = primary;
    if (preds >= 0) {
        int32_t filter = xn_new(ps->prog, XN_FILTER);
        if (filter < 0) {              /* GCOVR_EXCL_BR_LINE: alloc */
            fail(ps, "out of memory"); /* GCOVR_EXCL_LINE */
            return -1;                 /* GCOVR_EXCL_LINE */
        }
        ps->prog->nodes[filter].first = primary;
        ps->prog->nodes[filter].second = preds;
        base = filter;
    }
    if (ps->lx.kind != TK_SLASH && ps->lx.kind != TK_DSLASH) {
        return base;
    }
    int32_t path = xn_new(ps->prog, XN_PATH);
    if (path < 0) {                /* GCOVR_EXCL_BR_LINE: alloc */
        fail(ps, "out of memory"); /* GCOVR_EXCL_LINE */
        return -1;                 /* GCOVR_EXCL_LINE */
    }
    ps->prog->nodes[path].second = base;
    int32_t head = -1;
    int32_t tail = -1;
    while ((ps->lx.kind == TK_SLASH || ps->lx.kind == TK_DSLASH)) {
        int dslash = ps->lx.kind == TK_DSLASH;
        lex_next(&ps->lx);
        parse_relative_tail(ps, &head, &tail, dslash);
    }
    ps->prog->nodes[path].first = head;
    return path;
}

static int32_t parse_union(parser *ps) {
    int32_t left = parse_filter_or_path(ps);
    while (ps->lx.kind == TK_PIPE) {
        lex_next(&ps->lx);
        int32_t right = parse_filter_or_path(ps);
        int32_t union_node = xn_new(ps->prog, XN_UNION);
        if (union_node < 0) { /* GCOVR_EXCL_BR_LINE: arena allocation failure cannot be forced */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        ps->prog->nodes[union_node].first = left;
        ps->prog->nodes[union_node].second = right;
        left = union_node;
    }
    return left;
}

/* Every recursion cycle -- a nested group, a predicate, a function argument, or a chain
   of unary minus -- descends through here, so a single depth cap bounds the whole
   parser's stack use and raises past the bound instead of overflowing (issue #421). */
static int32_t parse_unary(parser *ps) {
    if (ps->depth >= XP_MAX_DEPTH) {
        fail(ps, "expression nested too deeply");
        return -1;
    }
    ps->depth++;
    int32_t result;
    if (ps->lx.kind == TK_MINUS) {
        lex_next(&ps->lx);
        int32_t operand = parse_unary(ps);
        int32_t neg = xn_new(ps->prog, XN_NEG);
        if (neg < 0) {   /* GCOVR_EXCL_BR_LINE: arena allocation failure cannot be forced */
            ps->depth--; /* GCOVR_EXCL_LINE */
            return -1;   /* GCOVR_EXCL_LINE */
        }
        ps->prog->nodes[neg].first = operand;
        result = neg;
    } else {
        result = parse_union(ps);
    }
    ps->depth--;
    return result;
}

static int32_t parse_multiplicative(parser *ps) {
    int32_t left = parse_unary(ps);
    for (;;) {
        enum xn_kind op;
        if (ps->lx.kind == TK_STAR) {
            op = XN_MUL;
        } else if (ps->lx.kind == TK_DIV) {
            op = XN_DIV;
        } else if (ps->lx.kind == TK_MOD) {
            op = XN_MOD;
        } else {
            break;
        }
        lex_next(&ps->lx);
        int32_t right = parse_unary(ps);
        int32_t node = xn_new(ps->prog, op);
        if (node < 0) { /* GCOVR_EXCL_BR_LINE: arena allocation failure cannot be forced */
            return -1;  /* GCOVR_EXCL_LINE */
        }
        ps->prog->nodes[node].first = left;
        ps->prog->nodes[node].second = right;
        left = node;
    }
    return left;
}

static int32_t parse_additive(parser *ps) {
    int32_t left = parse_multiplicative(ps);
    for (;;) {
        enum xn_kind op;
        if (ps->lx.kind == TK_PLUS) {
            op = XN_ADD;
        } else if (ps->lx.kind == TK_MINUS) {
            op = XN_SUB;
        } else {
            break;
        }
        lex_next(&ps->lx);
        int32_t right = parse_multiplicative(ps);
        int32_t node = xn_new(ps->prog, op);
        if (node < 0) { /* GCOVR_EXCL_BR_LINE: arena allocation failure cannot be forced */
            return -1;  /* GCOVR_EXCL_LINE */
        }
        ps->prog->nodes[node].first = left;
        ps->prog->nodes[node].second = right;
        left = node;
    }
    return left;
}

static int32_t parse_relational(parser *ps) {
    int32_t left = parse_additive(ps);
    for (;;) {
        enum xn_kind op;
        if (ps->lx.kind == TK_LT) {
            op = XN_LT;
        } else if (ps->lx.kind == TK_LE) {
            op = XN_LE;
        } else if (ps->lx.kind == TK_GT) {
            op = XN_GT;
        } else if (ps->lx.kind == TK_GE) {
            op = XN_GE;
        } else {
            break;
        }
        lex_next(&ps->lx);
        int32_t right = parse_additive(ps);
        int32_t node = xn_new(ps->prog, op);
        if (node < 0) { /* GCOVR_EXCL_BR_LINE: arena allocation failure cannot be forced */
            return -1;  /* GCOVR_EXCL_LINE */
        }
        ps->prog->nodes[node].first = left;
        ps->prog->nodes[node].second = right;
        left = node;
    }
    return left;
}

static int32_t parse_equality(parser *ps) {
    int32_t left = parse_relational(ps);
    for (;;) {
        enum xn_kind op;
        if (ps->lx.kind == TK_EQ) {
            op = XN_EQ;
        } else if (ps->lx.kind == TK_NE) {
            op = XN_NE;
        } else {
            break;
        }
        lex_next(&ps->lx);
        int32_t right = parse_relational(ps);
        int32_t node = xn_new(ps->prog, op);
        if (node < 0) { /* GCOVR_EXCL_BR_LINE: arena allocation failure cannot be forced */
            return -1;  /* GCOVR_EXCL_LINE */
        }
        ps->prog->nodes[node].first = left;
        ps->prog->nodes[node].second = right;
        left = node;
    }
    return left;
}

static int32_t parse_and(parser *ps) {
    int32_t left = parse_equality(ps);
    while (ps->lx.kind == TK_AND) {
        lex_next(&ps->lx);
        int32_t right = parse_equality(ps);
        int32_t node = xn_new(ps->prog, XN_AND);
        if (node < 0) { /* GCOVR_EXCL_BR_LINE: arena allocation failure cannot be forced */
            return -1;  /* GCOVR_EXCL_LINE */
        }
        ps->prog->nodes[node].first = left;
        ps->prog->nodes[node].second = right;
        left = node;
    }
    return left;
}

static int32_t parse_expr(parser *ps) {
    int32_t left = parse_and(ps);
    while (ps->lx.kind == TK_OR) {
        lex_next(&ps->lx);
        int32_t right = parse_and(ps);
        int32_t node = xn_new(ps->prog, XN_OR);
        if (node < 0) { /* GCOVR_EXCL_BR_LINE: arena allocation failure cannot be forced */
            return -1;  /* GCOVR_EXCL_LINE */
        }
        ps->prog->nodes[node].first = left;
        ps->prog->nodes[node].second = right;
        left = node;
    }
    return left;
}

/* ---------------------------------------------------------- public API */

static int func_name_is(const xn *node, const char *name) {
    Py_ssize_t length = (Py_ssize_t)strlen(name);
    if (node->str_len != length) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < length; index++) {
        if (node->str[index] != (Py_UCS4)(unsigned char)name[index]) {
            return 0;
        }
    }
    return 1;
}

static int func_name_in(const xn *node, const char *const *names) {
    for (int index = 0; names[index] != NULL; index++) {
        if (func_name_is(node, names[index])) {
            return 1;
        }
    }
    return 0;
}

/* The functions that return a number; a predicate that calls one is positional. */
static const char *const NUMERIC_FUNCS[] = {"last",  "position", "count", "sum",    "string-length",
                                            "floor", "ceiling",  "round", "number", NULL};
static const char *const POSITION_FUNCS[] = {"position", "last", NULL};

/* Whether an expression calls position() or last() anywhere within it. */
static int references_position(const xp_program *prog, int32_t index) {
    if (index < 0) {
        return 0;
    }
    const xn *node = &prog->nodes[index];
    if (node->kind == XN_FUNC && func_name_in(node, POSITION_FUNCS)) {
        return 1;
    }
    return references_position(prog, node->first) || references_position(prog, node->second) ||
           references_position(prog, node->next);
}

/* A predicate is positional (so its result depends on the axis it runs over) when it
   yields a number -- a bare [n], an arithmetic expression, or a numeric function --
   or when it mentions position()/last(). Such a predicate makes the //X collapse
   unsafe, because //p[1] is the first p child of each parent, not descendant::p[1]. */
static int predicate_is_positional(const xp_program *prog, int32_t index) {
    const xn *node = &prog->nodes[index];
    if (node->kind == XN_NUM || (node->kind >= XN_ADD && node->kind <= XN_NEG)) {
        return 1;
    }
    if (node->kind == XN_FUNC && func_name_in(node, NUMERIC_FUNCS)) {
        return 1;
    }
    return references_position(prog, index);
}

static int step_has_positional_predicate(const xp_program *prog, int32_t step_index) {
    for (int32_t pred = prog->nodes[step_index].first; pred >= 0; pred = prog->nodes[pred].next) {
        if (predicate_is_positional(prog, prog->nodes[pred].first)) {
            return 1;
        }
    }
    return 0;
}

/* Collapse `descendant-or-self::node()/child::X` (the `//X` abbreviation) into a
   single `descendant::X` step, so `//a` is one descendant walk testing for a rather
   than materializing every node and then filtering its children. The two forms are
   equivalent -- an element is a child of some node in descendant-or-self(context) iff
   it is a (proper) descendant of context -- as long as the child step carries no
   positional predicate, whose meaning differs between the two axes. */
static void optimize_descendant_steps(xp_program *prog) {
    for (int32_t index = 0; index < prog->count; index++) {
        if (prog->nodes[index].kind != XN_PATH) {
            continue;
        }
        for (int32_t cursor = prog->nodes[index].first; cursor >= 0; cursor = prog->nodes[cursor].next) {
            xn *step = &prog->nodes[cursor];
            if (step->axis != AX_DESCENDANT_OR_SELF || step->test != NT_NODE || step->first >= 0 || step->next < 0) {
                continue;
            }
            xn *follow = &prog->nodes[step->next];
            if (follow->axis != AX_CHILD || step_has_positional_predicate(prog, step->next)) {
                continue;
            }
            step->axis = AX_DESCENDANT;
            step->test = follow->test;
            PyMem_Free(step->str); /* the node() test owns no name */
            step->str = follow->str;
            step->str_len = follow->str_len;
            step->prefix_len = follow->prefix_len;
            follow->str = NULL; /* ownership moved to step */
            step->first = follow->first;
            step->next = follow->next;
        }
    }
}

/* Write the parse error into errbuf: the reason, the source offset, and a short ASCII
   slice of the offending token (non-ASCII code points shown as '?'), or a note that the
   expression ended early when the failure is at end of input. */
static void format_error(char *errbuf, size_t errlen, const parser *ps) {
    /* ps->msg is NULL only when an arena OOM failed the parse without a message, which
       cannot be forced from a test */
    const char *reason = ps->msg != NULL ? ps->msg : "invalid XPath expression"; /* GCOVR_EXCL_BR_LINE */
    char token[32];
    Py_ssize_t span = ps->err_tok_len > 0 ? ps->err_tok_len : (ps->err_pos < ps->lx.len ? 1 : 0);
    Py_ssize_t count = span < (Py_ssize_t)sizeof(token) - 1 ? span : (Py_ssize_t)sizeof(token) - 1;
    for (Py_ssize_t index = 0; index < count; index++) {
        Py_UCS4 ch = ps->err_tok[index];
        token[index] = ch < 0x80 ? (char)ch : '?';
    }
    token[count] = '\0';
    if (count > 0) {
        snprintf(errbuf, errlen, "%s at offset %zd near '%s'", reason, ps->err_pos, token);
    } else {
        snprintf(errbuf, errlen, "%s at the end of the expression", reason);
    }
}

xp_program *xp_compile(const Py_UCS4 *src, Py_ssize_t len, char *errbuf, size_t errlen) {
    xp_program *prog = PyMem_Malloc(sizeof(*prog));
    if (prog == NULL) {                            /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        snprintf(errbuf, errlen, "out of memory"); /* GCOVR_EXCL_LINE */
        return NULL;                               /* GCOVR_EXCL_LINE */
    }
    prog->nodes = NULL;
    prog->count = 0;
    prog->cap = 0;
    prog->root = -1;
    parser ps = {0};
    ps.prog = prog;
    ps.lx.src = src;
    ps.lx.len = len;
    ps.lx.op_context = 0;
    lex_next(&ps.lx);
    if (ps.lx.error) {
        fail(&ps, "invalid character in expression");
    }
    prog->root = parse_expr(&ps);
    if (ps.lx.error) {
        /* a stray character that made the lexer stop where the parser also stopped
           (e.g. a lone '!') would otherwise look like a clean end of input */
        fail(&ps, "invalid character in expression");
    }
    if (!ps.failed && ps.lx.kind != TK_EOF) {
        fail(&ps, "unexpected trailing tokens");
    }
    /* root < 0 and a NULL message only arise from an arena allocation failure that
       did not record a message, so those branches cannot be forced from a test */
    if (ps.failed || prog->root < 0) { /* GCOVR_EXCL_BR_LINE: root < 0 is an unforced arena OOM */
        format_error(errbuf, errlen, &ps);
        xp_free(prog);
        return NULL;
    }
    optimize_descendant_steps(prog);
    return prog;
}

void xp_free(xp_program *prog) {
    if (prog == NULL) { /* GCOVR_EXCL_BR_LINE: callers never pass NULL */
        return;         /* GCOVR_EXCL_LINE */
    }
    for (int32_t index = 0; index < prog->count; index++) {
        PyMem_Free(prog->nodes[index].str);
    }
    PyMem_Free(prog->nodes);
    PyMem_Free(prog);
}
