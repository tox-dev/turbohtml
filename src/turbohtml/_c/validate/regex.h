/* A compact regular-expression matcher for the XSD `pattern` facet (and the RELAX NG
   equivalent). The pattern language is the XSD 1.0 regex subset: literals, `.`, escape
   classes (\d \D \w \W \s \S), character classes with ranges and negation, grouping,
   alternation, and the `? * + {m} {m,} {m,n}` quantifiers. It compiles to a Thompson
   NFA (Cox, "Regular Expression Matching Can Be Simple And Fast") and simulates it, so
   matching is linear in the input with no backtracking blow-up. Matching is anchored:
   the whole value must be consumed, as a facet requires. The parser is total -- an
   unrecognized metacharacter is taken as a literal -- so a schema never fails to
   compile over its pattern. Included once into datatypes.h; every definition is static. */

#ifndef TURBOHTML_VALIDATE_REGEX_H
#define TURBOHTML_VALIDATE_REGEX_H

enum {
    RX_BD = 1,  /* \d */
    RX_BW = 2,  /* \w */
    RX_BS = 4,  /* \s */
    RX_ND = 8,  /* \D */
    RX_NW = 16, /* \W */
    RX_NS = 32, /* \S */
};

typedef struct {
    Py_UCS4 lo, hi;
} rrange;

typedef struct {
    rrange *ranges;
    int range_count;
    int builtins;
    int negate;
} rclass;

enum { RN_EMPTY, RN_CHAR, RN_ANY, RN_CLASS, RN_CONCAT, RN_ALT, RN_QUEST, RN_STAR, RN_PLUS, RN_REPEAT };

typedef struct rnode {
    int type;
    Py_UCS4 ch;
    rclass *cls;
    struct rnode *a, *b;
    int rmin, rmax;
} rnode;

enum { RS_SPLIT, RS_MATCH, RS_ACCEPT };

typedef struct rstate {
    int kind;
    int mkind; /* RN_CHAR / RN_ANY / RN_CLASS */
    Py_UCS4 ch;
    rclass *cls;
    struct rstate *out, *out1;
    unsigned gen;
} rstate;

typedef struct {
    const Py_UCS4 *pattern;
    Py_ssize_t len, pos;
    arena *mem;
    int failed;
} rparser;

static rnode *rx_node(rparser *parser, int type) {
    rnode *node = arena_alloc(parser->mem, sizeof(rnode));
    if (node == NULL) {     /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        parser->failed = 1; /* GCOVR_EXCL_LINE */
        return NULL;        /* GCOVR_EXCL_LINE */
    }
    memset(node, 0, sizeof(*node));
    node->type = type;
    node->rmax = -1;
    return node;
}

static rnode *rx_parse_alt(rparser *parser);

static int rx_range_push(rparser *parser, rclass *cls, Py_UCS4 lo, Py_UCS4 hi) {
    rrange *grown = arena_alloc(parser->mem, (size_t)(cls->range_count + 1) * sizeof(rrange));
    if (grown == NULL) {    /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        parser->failed = 1; /* GCOVR_EXCL_LINE */
        return -1;          /* GCOVR_EXCL_LINE */
    }
    if (cls->range_count > 0) {
        memcpy(grown, cls->ranges, (size_t)cls->range_count * sizeof(rrange));
    }
    grown[cls->range_count].lo = lo;
    grown[cls->range_count].hi = hi;
    cls->ranges = grown;
    cls->range_count++;
    return 0;
}

/* Map a class escape letter to its builtin flag, or 0 when it is not one. */
static int rx_builtin_flag(Py_UCS4 letter) {
    switch (letter) {
    case 'd':
        return RX_BD;
    case 'w':
        return RX_BW;
    case 's':
        return RX_BS;
    case 'D':
        return RX_ND;
    case 'W':
        return RX_NW;
    case 'S':
        return RX_NS;
    default:
        return 0;
    }
}

/* The literal a backslash escape stands for (\n \t \r or the escaped metacharacter). */
static Py_UCS4 rx_escape_literal(Py_UCS4 letter) {
    switch (letter) {
    case 'n':
        return '\n';
    case 't':
        return '\t';
    case 'r':
        return '\r';
    default:
        return letter;
    }
}

static rnode *rx_parse_class(rparser *parser) {
    rnode *node = rx_node(parser, RN_CLASS);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    rclass *cls = arena_alloc(parser->mem, sizeof(rclass));
    if (cls == NULL) {      /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        parser->failed = 1; /* GCOVR_EXCL_LINE */
        return NULL;        /* GCOVR_EXCL_LINE */
    }
    memset(cls, 0, sizeof(*cls));
    node->cls = cls;
    if (parser->pos < parser->len && parser->pattern[parser->pos] == '^') {
        cls->negate = 1;
        parser->pos++;
    }
    while (parser->pos < parser->len && parser->pattern[parser->pos] != ']') {
        Py_UCS4 current = parser->pattern[parser->pos++];
        if (current == '\\' && parser->pos < parser->len) {
            Py_UCS4 letter = parser->pattern[parser->pos++];
            int flag = rx_builtin_flag(letter);
            if (flag != 0) {
                cls->builtins |= flag;
                continue;
            }
            current = rx_escape_literal(letter);
        }
        if (parser->pos + 1 < parser->len && parser->pattern[parser->pos] == '-' &&
            parser->pattern[parser->pos + 1] != ']') {
            Py_UCS4 hi = parser->pattern[parser->pos + 1];
            parser->pos += 2;
            if (hi == '\\' && parser->pos < parser->len) {
                hi = rx_escape_literal(parser->pattern[parser->pos++]);
            }
            if (rx_range_push(parser, cls, current, hi) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
                return NULL;                                   /* GCOVR_EXCL_LINE */
            }
        } else if (rx_range_push(parser, cls, current, current) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM unforceable */
            return NULL;                                               /* GCOVR_EXCL_LINE */
        }
    }
    if (parser->pos < parser->len) { /* consume the closing ']' when present */
        parser->pos++;
    }
    return node;
}

static rnode *rx_class_from_builtin(rparser *parser, int flag) {
    rnode *node = rx_node(parser, RN_CLASS);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    rclass *cls = arena_alloc(parser->mem, sizeof(rclass));
    if (cls == NULL) {      /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        parser->failed = 1; /* GCOVR_EXCL_LINE */
        return NULL;        /* GCOVR_EXCL_LINE */
    }
    memset(cls, 0, sizeof(*cls));
    cls->builtins = flag;
    node->cls = cls;
    return node;
}

static rnode *rx_parse_atom(rparser *parser) {
    Py_UCS4 current = parser->pattern[parser->pos];
    if (current == '(') {
        parser->pos++;
        rnode *inner = rx_parse_alt(parser);
        /* rx_parse_alt stops at ')' or end of input, so a remaining char here is the ')' */
        if (parser->pos < parser->len) {
            parser->pos++;
        }
        return inner;
    }
    if (current == '[') {
        parser->pos++;
        return rx_parse_class(parser);
    }
    if (current == '.') {
        parser->pos++;
        return rx_node(parser, RN_ANY);
    }
    if (current == '\\' && parser->pos + 1 < parser->len) {
        Py_UCS4 letter = parser->pattern[parser->pos + 1];
        int flag = rx_builtin_flag(letter);
        parser->pos += 2;
        if (flag != 0) {
            return rx_class_from_builtin(parser, flag);
        }
        rnode *node = rx_node(parser, RN_CHAR);
        if (node != NULL) { /* GCOVR_EXCL_BR_LINE: node is NULL only on unforceable arena OOM */
            node->ch = rx_escape_literal(letter);
        }
        return node;
    }
    parser->pos++;
    rnode *node = rx_node(parser, RN_CHAR);
    if (node != NULL) { /* GCOVR_EXCL_BR_LINE: node is NULL only on unforceable arena OOM */
        node->ch = current;
    }
    return node;
}

/* Parse a {m}, {m,} or {m,n} quantifier starting at the '{'. Returns 1 with rmin and
   rmax set (rmax -1 for unbounded), or 0 leaving pos unchanged when not a quantifier. */
static int rx_parse_bound(rparser *parser, int *rmin, int *rmax) {
    Py_ssize_t save = parser->pos;
    parser->pos++; /* past '{' */
    Py_ssize_t low = 0, digits = 0;
    while (parser->pos < parser->len && parser->pattern[parser->pos] >= '0' && parser->pattern[parser->pos] <= '9') {
        low = low * 10 + (parser->pattern[parser->pos++] - '0');
        digits++;
    }
    if (digits == 0) {
        parser->pos = save;
        return 0;
    }
    int high = (int)low;
    if (parser->pos < parser->len && parser->pattern[parser->pos] == ',') {
        parser->pos++;
        Py_ssize_t hi = 0, hi_digits = 0;
        while (parser->pos < parser->len && parser->pattern[parser->pos] >= '0' &&
               parser->pattern[parser->pos] <= '9') {
            hi = hi * 10 + (parser->pattern[parser->pos++] - '0');
            hi_digits++;
        }
        high = hi_digits == 0 ? -1 : (int)hi;
    }
    if (parser->pos >= parser->len || parser->pattern[parser->pos] != '}') {
        parser->pos = save;
        return 0;
    }
    parser->pos++;
    *rmin = (int)low;
    *rmax = high;
    return 1;
}

static rnode *rx_parse_quant(rparser *parser) {
    rnode *atom = rx_parse_atom(parser);
    if (atom == NULL || parser->pos >= parser->len) { /* GCOVR_EXCL_BR_LINE: NULL only on unforceable arena OOM */
        return atom;
    }
    Py_UCS4 quant = parser->pattern[parser->pos];
    int type = quant == '?' ? RN_QUEST : (quant == '*' ? RN_STAR : (quant == '+' ? RN_PLUS : -1));
    if (type != -1) {
        parser->pos++;
        rnode *node = rx_node(parser, type);
        if (node != NULL) { /* GCOVR_EXCL_BR_LINE: node is NULL only on unforceable arena OOM */
            node->a = atom;
        }
        return node;
    }
    if (quant == '{') {
        int rmin, rmax;
        if (rx_parse_bound(parser, &rmin, &rmax)) {
            rnode *node = rx_node(parser, RN_REPEAT);
            if (node != NULL) { /* GCOVR_EXCL_BR_LINE: node is NULL only on unforceable arena OOM */
                node->a = atom;
                node->rmin = rmin;
                node->rmax = rmax;
            }
            return node;
        }
    }
    return atom;
}

static rnode *rx_parse_concat(rparser *parser) {
    rnode *left = NULL;
    while (parser->pos < parser->len && parser->pattern[parser->pos] != '|' && parser->pattern[parser->pos] != ')') {
        rnode *piece = rx_parse_quant(parser);
        if (piece == NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on unforceable arena OOM */
            return NULL;     /* GCOVR_EXCL_LINE */
        }
        if (left == NULL) {
            left = piece;
        } else {
            rnode *cat = rx_node(parser, RN_CONCAT);
            if (cat == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
                return NULL;   /* GCOVR_EXCL_LINE */
            }
            cat->a = left;
            cat->b = piece;
            left = cat;
        }
    }
    return left == NULL ? rx_node(parser, RN_EMPTY) : left;
}

static rnode *rx_parse_alt(rparser *parser) {
    rnode *left = rx_parse_concat(parser);
    while (parser->pos < parser->len && parser->pattern[parser->pos] == '|') {
        parser->pos++;
        rnode *right = rx_parse_concat(parser);
        rnode *alt = rx_node(parser, RN_ALT);
        if (left == NULL || right == NULL || alt == NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on unforceable arena OOM */
            return NULL;                                    /* GCOVR_EXCL_LINE */
        }
        alt->a = left;
        alt->b = right;
        left = alt;
    }
    return left;
}

static rstate *rx_state(arena *mem, int kind) {
    rstate *state = arena_alloc(mem, sizeof(rstate));
    if (state == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    memset(state, 0, sizeof(*state));
    state->kind = kind;
    return state;
}

static rstate *rx_compile(arena *mem, rnode *node, rstate *out);

static rstate *rx_compile_repeat(arena *mem, rnode *node, int rmin, int rmax, rstate *out) {
    if (rmin > 0) {
        rstate *tail = rx_compile_repeat(mem, node, rmin - 1, rmax < 0 ? -1 : rmax - 1, out);
        return tail == NULL ? NULL : rx_compile(mem, node->a, tail); /* GCOVR_EXCL_BR_LINE: NULL only on OOM */
    }
    if (rmax < 0) {
        rstate *split = rx_state(mem, RS_SPLIT);
        if (split == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return NULL;     /* GCOVR_EXCL_LINE */
        }
        split->out1 = out;
        split->out = rx_compile(mem, node->a, split);
        return split;
    }
    if (rmax == 0) {
        return out;
    }
    rstate *split = rx_state(mem, RS_SPLIT);
    if (split == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    rstate *tail = rx_compile_repeat(mem, node, 0, rmax - 1, out);
    split->out1 = out;
    split->out = tail == NULL ? NULL : rx_compile(mem, node->a, tail); /* GCOVR_EXCL_BR_LINE: NULL only on OOM */
    return split;
}

/* Compile an AST node into an NFA fragment whose every exit flows to `out`. */
static rstate *rx_compile(arena *mem, rnode *node, rstate *out) {
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on unforceable arena OOM */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    switch (node->type) {
    case RN_EMPTY:
        return out;
    case RN_CHAR:
    case RN_ANY:
    case RN_CLASS: {
        rstate *state = rx_state(mem, RS_MATCH);
        if (state == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return NULL;     /* GCOVR_EXCL_LINE */
        }
        state->mkind = node->type;
        state->ch = node->ch;
        state->cls = node->cls;
        state->out = out;
        return state;
    }
    case RN_CONCAT: {
        rstate *tail = rx_compile(mem, node->b, out);
        return tail == NULL ? NULL : rx_compile(mem, node->a, tail); /* GCOVR_EXCL_BR_LINE: NULL only on OOM */
    }
    case RN_ALT: {
        rstate *split = rx_state(mem, RS_SPLIT);
        if (split == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return NULL;     /* GCOVR_EXCL_LINE */
        }
        split->out = rx_compile(mem, node->a, out);
        split->out1 = rx_compile(mem, node->b, out);
        return split;
    }
    case RN_QUEST: {
        rstate *split = rx_state(mem, RS_SPLIT);
        if (split == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return NULL;     /* GCOVR_EXCL_LINE */
        }
        split->out = rx_compile(mem, node->a, out);
        split->out1 = out;
        return split;
    }
    case RN_STAR: {
        rstate *split = rx_state(mem, RS_SPLIT);
        if (split == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return NULL;     /* GCOVR_EXCL_LINE */
        }
        split->out1 = out;
        split->out = rx_compile(mem, node->a, split);
        return split;
    }
    case RN_PLUS: {
        rstate *split = rx_state(mem, RS_SPLIT);
        if (split == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return NULL;     /* GCOVR_EXCL_LINE */
        }
        split->out1 = out;
        rstate *start = rx_compile(mem, node->a, split);
        split->out = start;
        return start;
    }
    default:
        return rx_compile_repeat(mem, node, node->rmin, node->rmax, out);
    }
}

static int rx_class_match(const rclass *cls, Py_UCS4 codepoint) {
    int inside = 0;
    for (int index = 0; index < cls->range_count; index++) {
        if (codepoint >= cls->ranges[index].lo && codepoint <= cls->ranges[index].hi) {
            inside = 1;
        }
    }
    int is_d = is_digit(codepoint);
    int is_w = is_name_char(codepoint) && codepoint != '-' && codepoint != '.';
    int is_s = is_xml_space(codepoint);
    if ((cls->builtins & RX_BD) && is_d) {
        inside = 1;
    }
    if ((cls->builtins & RX_BW) && is_w) {
        inside = 1;
    }
    if ((cls->builtins & RX_BS) && is_s) {
        inside = 1;
    }
    if ((cls->builtins & RX_ND) && !is_d) {
        inside = 1;
    }
    if ((cls->builtins & RX_NW) && !is_w) {
        inside = 1;
    }
    if ((cls->builtins & RX_NS) && !is_s) {
        inside = 1;
    }
    return cls->negate ? !inside : inside;
}

static int rx_state_match(const rstate *state, Py_UCS4 codepoint) {
    if (state->mkind == RN_CHAR) {
        return state->ch == codepoint;
    }
    if (state->mkind == RN_ANY) {
        return codepoint != '\n' && codepoint != '\r';
    }
    return rx_class_match(state->cls, codepoint);
}

typedef struct {
    rstate **items;
    int len;
} rlist;

static void rx_add(rlist *list, rstate *state, unsigned gen) {
    if (state->gen == gen) { /* every out-edge in a compiled fragment points at a real state, never NULL */
        return;
    }
    state->gen = gen;
    if (state->kind == RS_SPLIT) {
        rx_add(list, state->out, gen);
        rx_add(list, state->out1, gen);
        return;
    }
    list->items[list->len++] = state;
}

/* Whether the whole [value, value+len) is matched by the pattern. */
static int regex_full_match(arena *mem, const Py_UCS4 *pattern, Py_ssize_t pattern_len, const Py_UCS4 *value,
                            Py_ssize_t len) {
    rparser parser = {pattern, pattern_len, 0, mem, 0};
    rnode *ast = rx_parse_alt(&parser);
    rstate *accept = rx_state(mem, RS_ACCEPT);
    if (parser.failed || ast == NULL || accept == NULL) { /* GCOVR_EXCL_BR_LINE: only on unforceable arena OOM */
        return 1;                                         /* GCOVR_EXCL_LINE */
    }
    rstate *start = rx_compile(mem, ast, accept);
    /* An NFA over N states visits at most N per step; two state lists of that size hold
       every reachable state without a growth check. */
    Py_ssize_t capacity = pattern_len * 4 + 8;
    rstate **storage = arena_alloc(mem, (size_t)capacity * 2 * sizeof(rstate *));
    if (start == NULL || storage == NULL) { /* GCOVR_EXCL_BR_LINE: only on unforceable arena OOM */
        return 1;                           /* GCOVR_EXCL_LINE */
    }
    rlist current = {storage, 0};
    rlist next = {storage + capacity, 0};
    unsigned gen = 1;
    rx_add(&current, start, gen);
    for (Py_ssize_t index = 0; index < len; index++) {
        gen++;
        next.len = 0;
        for (int state = 0; state < current.len; state++) {
            if (current.items[state]->kind == RS_MATCH && rx_state_match(current.items[state], value[index])) {
                rx_add(&next, current.items[state]->out, gen);
            }
        }
        rlist swap = current;
        current = next;
        next = swap;
    }
    for (int state = 0; state < current.len; state++) {
        if (current.items[state]->kind == RS_ACCEPT) {
            return 1;
        }
    }
    return 0;
}

#endif /* TURBOHTML_VALIDATE_REGEX_H */
