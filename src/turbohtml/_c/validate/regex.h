/* A compact regular-expression matcher for the XSD `pattern` facet (and the RELAX NG
   equivalent). The pattern language is the XSD 1.0 regex subset: literals, `.`, escape
   classes (\d \D \w \W \s \S \i \I \c \C), character classes with ranges and negation, grouping,
   alternation, and the `? * + {m} {m,} {m,n}` quantifiers. It compiles to a Thompson
   NFA (Cox, "Regular Expression Matching Can Be Simple And Fast") and simulates it, so
   matching is linear in the input with no backtracking blow-up. Matching is anchored:
   the whole value must be consumed, as a facet requires. The parser is total -- an
   unrecognized metacharacter is taken as a literal -- so a schema never fails to
   compile over its pattern. Included once into datatypes.h; every definition is static. */

#ifndef TURBOHTML_VALIDATE_REGEX_H
#define TURBOHTML_VALIDATE_REGEX_H

#include "data/xsd_digit_table.h"
#include "data/xsd_word_table.h"

/* Bounds that keep an adversarial pattern from exhausting the C stack or memory. The quantifier compiler is iterative,
   so any repeat count is stack-safe and only the total NFA state count is bounded. Group nesting drives the recursive
   parser, so it takes the 250 levels PCRE2 (PARENS_NEST_LIMIT) and Rust's regex-syntax (nest_limit) allow for the
   same reason: a musl thread from uv's python-build-standalone gets a 130 KiB stack, which 1000 levels overflow. */
#define RX_MAX_GROUP_DEPTH TH_DEPTH_LIMIT(250)

/* Why a pattern failed to compile; each maps to its own error in regex_cache_add. */
enum { RX_FAIL_MEMORY = 1, RX_FAIL_NESTING, RX_FAIL_CLASS_NESTING, RX_FAIL_BOUND, RX_FAIL_STATES };
#define RX_MAX_STATES 2000000

enum {
    RX_BD = 1,   /* \d */
    RX_BW = 2,   /* \w */
    RX_BS = 4,   /* \s */
    RX_ND = 8,   /* \D */
    RX_NW = 16,  /* \W */
    RX_NS = 32,  /* \S */
    RX_BI = 64,  /* \i */
    RX_NI = 128, /* \I */
    RX_BC = 256, /* \c */
    RX_NC = 512, /* \C */
};

typedef struct {
    Py_UCS4 lo, hi;
} rrange;

typedef struct rclass {
    rrange *ranges;
    Py_ssize_t range_count, range_cap;
    int builtins;
    int negate;
    struct rclass *subtract;
} rclass;

enum { RN_EMPTY, RN_CHAR, RN_ANY, RN_CLASS, RN_BUILTIN, RN_CONCAT, RN_ALT, RN_QUEST, RN_STAR, RN_PLUS, RN_REPEAT };

typedef struct rnode {
    int type;
    Py_UCS4 ch;
    rclass *cls;
    struct rnode *a, *b;
    int rmin, rmax;
} rnode;

enum { RS_SPLIT, RS_MATCH, RS_ACCEPT, RS_SUB_MATCH };

typedef struct rstate {
    int kind;
    int mkind; /* RN_CHAR / RN_ANY / RN_CLASS / RN_BUILTIN */
    Py_UCS4 ch;
    rclass *cls;
    struct rstate *out, *out1;
    size_t index;
} rstate;

typedef struct {
    const Py_UCS4 *pattern;
    Py_ssize_t len, pos;
    arena *mem;
    int failed;
    int has_subtraction;
    Py_ssize_t fail_pos, fail_end; /* the span a nesting or bound error points at */
    int depth;                     /* open groups on the recursion path, capped at RX_MAX_GROUP_DEPTH */
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
    if (cls->range_count == cls->range_cap) {
        size_t cap, bytes;
        const int fits =
            th_grow_cap((size_t)cls->range_count + 1, (size_t)cls->range_cap, 8, sizeof(rrange), &cap, &bytes);
        if (!fits) {            /* GCOVR_EXCL_BR_LINE: allocation size overflow */
            parser->failed = 1; /* GCOVR_EXCL_LINE: allocation size overflow */
            return -1;          /* GCOVR_EXCL_LINE: allocation size overflow */
        }
        rrange *grown = arena_alloc(parser->mem, bytes);
        if (grown == NULL) {    /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            parser->failed = 1; /* GCOVR_EXCL_LINE */
            return -1;          /* GCOVR_EXCL_LINE */
        }
        if (cls->range_count > 0) {
            memcpy(grown, cls->ranges, (size_t)cls->range_count * sizeof(rrange));
        }
        cls->ranges = grown;
        cls->range_cap = (Py_ssize_t)cap;
    }
    cls->ranges[cls->range_count].lo = lo;
    cls->ranges[cls->range_count].hi = hi;
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
    case 'i':
        return RX_BI;
    case 'I':
        return RX_NI;
    case 'c':
        return RX_BC;
    case 'C':
        return RX_NC;
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
        if (current == '-' && parser->pos < parser->len && parser->pattern[parser->pos] == '[' &&
            (cls->range_count > 0 || cls->builtins != 0)) {
            if (parser->depth >= RX_MAX_GROUP_DEPTH) {
                parser->failed = RX_FAIL_CLASS_NESTING;
                parser->fail_pos = parser->pos - 1;
                return NULL;
            }
            parser->pos++;
            parser->depth++;
            rnode *excluded = rx_parse_class(parser);
            parser->depth--;
            if (excluded == NULL) {
                return NULL;
            }
            cls->subtract = excluded->cls;
            parser->has_subtraction = 1;
            break;
        }
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
            parser->pattern[parser->pos + 1] != ']' && parser->pattern[parser->pos + 1] != '[') {
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

static rnode *rx_builtin_node(rparser *parser, int flag) {
    rnode *node = rx_node(parser, RN_BUILTIN);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    node->ch = (Py_UCS4)flag;
    return node;
}

static rnode *rx_parse_atom(rparser *parser) {
    Py_UCS4 current = parser->pattern[parser->pos];
    if (current == '(') {
        if (parser->depth >= RX_MAX_GROUP_DEPTH) {
            parser->failed = RX_FAIL_NESTING;
            parser->fail_pos = parser->pos;
            return NULL;
        }
        parser->pos++;
        parser->depth++;
        rnode *inner = rx_parse_alt(parser);
        parser->depth--;
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
            return rx_builtin_node(parser, flag);
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

/* Read a run of decimal digits as a quantifier count, saturating at the state budget: a larger count can never compile
   within it, and saturating keeps the arithmetic from overflowing and an empty-atom repeat such as (){999999999999}
   from looping without creating states. Returns the digit count. */
static Py_ssize_t rx_parse_count(rparser *parser, int *count) {
    Py_ssize_t digits = 0;
    int value = 0;
    while (parser->pos < parser->len && parser->pattern[parser->pos] >= '0' && parser->pattern[parser->pos] <= '9') {
        int digit = (int)(parser->pattern[parser->pos++] - '0');
        value = value > (RX_MAX_STATES - digit) / 10 ? RX_MAX_STATES : value * 10 + digit;
        digits++;
    }
    *count = value;
    return digits;
}

/* Parse a {m}, {m,} or {m,n} quantifier starting at the '{'. Returns 1 with rmin and
   rmax set (rmax -1 for unbounded), or 0 leaving pos unchanged when not a quantifier. */
static int rx_parse_bound(rparser *parser, int *rmin, int *rmax) {
    Py_ssize_t save = parser->pos;
    parser->pos++; /* past '{' */
    int low = 0;
    if (rx_parse_count(parser, &low) == 0) {
        parser->pos = save;
        return 0;
    }
    int high = low;
    if (parser->pos < parser->len && parser->pattern[parser->pos] == ',') {
        parser->pos++;
        int hi = 0;
        high = rx_parse_count(parser, &hi) == 0 ? -1 : hi;
    }
    if (parser->pos >= parser->len || parser->pattern[parser->pos] != '}') {
        parser->pos = save;
        return 0;
    }
    parser->pos++;
    if (high != -1 && low > high) {
        parser->failed = RX_FAIL_BOUND;
        parser->fail_pos = save;
        parser->fail_end = parser->pos;
    }
    *rmin = low;
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

typedef struct {
    arena *mem;
    size_t count;
    int failed;
    rstate sink; /* absorbs writes once the pattern is refused, so no caller dereferences NULL */
} rcompiler;

static rstate *rx_state(rcompiler *compiler, int kind) {
    rstate *state = NULL;
    if (compiler->count >= RX_MAX_STATES) {
        compiler->failed = RX_FAIL_STATES;
    } else if ((state = arena_alloc(compiler->mem, sizeof(rstate))) == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM */
        compiler->failed = RX_FAIL_MEMORY;                                     /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE: arena OOM */
    if (state == NULL) {
        /* a refused pattern keeps compiling into the sink, and the repeat loops stop once `failed` is set */
        state = &compiler->sink;
    }
    memset(state, 0, sizeof(*state));
    state->kind = kind;
    state->index = compiler->count++;
    return state;
}

static rstate *rx_compile(rcompiler *compiler, rnode *node, rstate *out);

/* Compile `node->a{rmin,rmax}` (rmax -1 for unbounded) iteratively, so a huge repeat count grows the NFA without
   growing the C stack; rx_state's budget bounds the memory, and the loops stop once it trips. An inverted bound such
   as {5,2} compiles as {5,}, as the recursive construction always did. */
static rstate *rx_compile_repeat(rcompiler *compiler, rnode *node, int rmin, int rmax, rstate *out) {
    if (rmax < rmin) {
        rmax = -1;
    }
    rstate *cur = out;
    if (rmax < 0) {
        rstate *split = rx_state(compiler, RS_SPLIT);
        split->out1 = out;
        split->out = rx_compile(compiler, node->a, split);
        cur = split;
    }
    for (int optional = rmax - rmin; optional > 0 && !compiler->failed; optional--) {
        rstate *split = rx_state(compiler, RS_SPLIT);
        split->out1 = out;
        split->out = rx_compile(compiler, node->a, cur);
        cur = split;
    }
    for (int required = 0; required < rmin && !compiler->failed; required++) {
        cur = rx_compile(compiler, node->a, cur);
    }
    return cur;
}

/* Compile an AST node into an NFA fragment whose every exit flows to `out`. */
static rstate *rx_compile(rcompiler *compiler, rnode *node, rstate *out) {
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on unforceable arena OOM */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    switch (node->type) {
    case RN_EMPTY:
        return out;
    case RN_CHAR:
    case RN_ANY:
    case RN_CLASS:
    case RN_BUILTIN: {
        rstate *state =
            rx_state(compiler, node->type == RN_CLASS && node->cls->subtract != NULL ? RS_SUB_MATCH : RS_MATCH);
        state->mkind = node->type;
        state->ch = node->ch;
        state->cls = node->cls;
        state->out = out;
        return state;
    }
    case RN_CONCAT: {
        /* the parser builds a concatenation as a left-leaning chain, so walk that spine iteratively: a long pattern
           has a chain as deep as its length, which recursion could not descend without overflowing the stack */
        rstate *tail = out;
        rnode *cur = node;
        while (cur->type == RN_CONCAT) {
            tail = rx_compile(compiler, cur->b, tail);
            cur = cur->a;
        }
        return rx_compile(compiler, cur, tail);
    }
    case RN_ALT: {
        /* likewise flatten the left-leaning alternation chain: each split's deep left child is threaded through `link`
           so a long `a|b|c|...` compiles without recursing once per alternative */
        rstate *result = NULL;
        rstate **link = &result;
        rnode *cur = node;
        while (cur->type == RN_ALT) {
            rstate *split = rx_state(compiler, RS_SPLIT);
            split->out1 = rx_compile(compiler, cur->b, out);
            *link = split;
            link = &split->out;
            cur = cur->a;
        }
        *link = rx_compile(compiler, cur, out);
        return result;
    }
    case RN_QUEST: {
        rstate *split = rx_state(compiler, RS_SPLIT);
        split->out = rx_compile(compiler, node->a, out);
        split->out1 = out;
        return split;
    }
    case RN_STAR: {
        rstate *split = rx_state(compiler, RS_SPLIT);
        split->out1 = out;
        split->out = rx_compile(compiler, node->a, split);
        return split;
    }
    case RN_PLUS: {
        rstate *split = rx_state(compiler, RS_SPLIT);
        split->out1 = out;
        rstate *start = rx_compile(compiler, node->a, split);
        split->out = start;
        return start;
    }
    default:
        return rx_compile_repeat(compiler, node, node->rmin, node->rmax, out);
    }
}

static int rx_word_match(Py_UCS4 codepoint) {
    if (codepoint < 128) {
        return (TH_WORD_ASCII_BITS[codepoint / 64] & (UINT64_C(1) << (codepoint % 64))) != 0;
    }
    uint8_t kind = TH_WORD_PAGE_KIND[codepoint / 256];
    if (kind < 2) {
        return kind;
    }
    return (TH_WORD_MIXED_BITS[kind - 2][(codepoint / 64) % 4] & (UINT64_C(1) << (codepoint % 64))) != 0;
}

static int rx_digit_match(Py_UCS4 codepoint) {
    if (is_digit(codepoint)) {
        return 1;
    }
    if (codepoint < 128) {
        return 0;
    }
    uint8_t kind = TH_DIGIT_PAGE_KIND[codepoint / 256];
    if (kind == 0) {
        return 0;
    }
    return (TH_DIGIT_MIXED_BITS[kind - 2][(codepoint / 64) % 4] & (UINT64_C(1) << (codepoint % 64))) != 0;
}

static int rx_class_match(const rclass *cls, Py_UCS4 codepoint) {
    int inside = 0;
    for (Py_ssize_t index = 0; index < cls->range_count; index++) {
        if (codepoint >= cls->ranges[index].lo && codepoint <= cls->ranges[index].hi) {
            inside = 1;
        }
    }
    int is_d = (cls->builtins & (RX_BD | RX_ND)) != 0 && rx_digit_match(codepoint);
    int is_w = (cls->builtins & (RX_BW | RX_NW)) != 0 && rx_word_match(codepoint);
    int is_s = (cls->builtins & (RX_BS | RX_NS)) != 0 && is_xml_space(codepoint);
    int is_i = (cls->builtins & (RX_BI | RX_NI)) != 0 && is_name_start(codepoint);
    int is_c = (cls->builtins & (RX_BC | RX_NC)) != 0 && is_name_char(codepoint);
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
    if ((cls->builtins & RX_BI) && is_i) {
        inside = 1;
    }
    if ((cls->builtins & RX_NI) && !is_i) {
        inside = 1;
    }
    if ((cls->builtins & RX_BC) && is_c) {
        inside = 1;
    }
    if ((cls->builtins & RX_NC) && !is_c) {
        inside = 1;
    }
    return cls->negate ? !inside : inside;
}

static int rx_subtracted_match(const rclass *cls, Py_UCS4 codepoint) {
    return rx_class_match(cls, codepoint) && (cls->subtract == NULL || !rx_subtracted_match(cls->subtract, codepoint));
}

static int rx_state_match(const rstate *state, Py_UCS4 codepoint) {
    if (state->mkind == RN_CHAR) {
        return state->ch == codepoint;
    }
    if (state->mkind == RN_ANY) {
        return codepoint != '\n' && codepoint != '\r';
    }
    if (state->mkind == RN_BUILTIN) {
        switch (state->ch) {
        case RX_BD:
            return rx_digit_match(codepoint);
        case RX_ND:
            return !rx_digit_match(codepoint);
        case RX_BW:
            return rx_word_match(codepoint);
        case RX_NW:
            return !rx_word_match(codepoint);
        case RX_BS:
            return is_xml_space(codepoint);
        case RX_NS:
            return !is_xml_space(codepoint);
        case RX_BI:
            return is_name_start(codepoint);
        case RX_NI:
            return !is_name_start(codepoint);
        case RX_BC:
            return is_name_char(codepoint);
        default:
            return !is_name_char(codepoint);
        }
    }
    return rx_class_match(state->cls, codepoint);
}

typedef struct rpattern {
    const Py_UCS4 *text;
    Py_ssize_t len;
    rstate *start;
    size_t count;
    int has_subtraction;
    struct rpattern *next;
} rpattern;

static int regex_cache_add(th_schema *schema, const Py_UCS4 *text, Py_ssize_t len) {
    for (rpattern *cached = schema->regex_patterns; cached != NULL; cached = cached->next) {
        if (u_eq_u(text, len, cached->text, cached->len)) {
            return 0;
        }
    }
    rpattern *pattern = arena_alloc(&schema->mem, sizeof(*pattern));
    if (pattern == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return -1;         /* GCOVR_EXCL_LINE */
    }
    rparser parser = {.pattern = text, .len = len, .mem = &schema->mem};
    rnode *ast = rx_parse_alt(&parser);
    rcompiler compiler = {.mem = &schema->mem};
    rstate *start = parser.failed ? NULL : rx_compile(&compiler, ast, rx_state(&compiler, RS_ACCEPT));
    const int failed = parser.failed ? parser.failed : compiler.failed;
    if (failed == RX_FAIL_MEMORY) { /* GCOVR_EXCL_BR_LINE: arena OOM */
        PyErr_NoMemory();           /* GCOVR_EXCL_LINE */
        return -1;                  /* GCOVR_EXCL_LINE */
    }
    if (failed == RX_FAIL_NESTING) {
        PyErr_Format(PyExc_ValueError,
                     "schema pattern nests groups deeper than %d levels at offset %zd; flatten the nested groups",
                     RX_MAX_GROUP_DEPTH, parser.fail_pos);
        return -1;
    }
    if (failed == RX_FAIL_CLASS_NESTING) {
        PyErr_Format(PyExc_ValueError,
                     "schema pattern nests character classes deeper than %d levels at offset %zd; flatten the class",
                     RX_MAX_GROUP_DEPTH, parser.fail_pos);
        return -1;
    }
    if (failed == RX_FAIL_BOUND) {
        PyObject *bound =
            PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, text + parser.fail_pos, parser.fail_end - parser.fail_pos);
        if (bound != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            PyErr_Format(PyExc_ValueError,
                         "schema pattern quantifier %U at offset %zd has its minimum above its maximum", bound,
                         parser.fail_pos);
            Py_DECREF(bound);
        }
        return -1;
    }
    if (failed == RX_FAIL_STATES) {
        PyErr_Format(PyExc_ValueError,
                     "schema pattern needs more than %d NFA states; lower its repeat counts or split it into "
                     "several patterns",
                     RX_MAX_STATES);
        return -1;
    }
    pattern->text = text;
    pattern->len = len;
    pattern->start = start;
    pattern->has_subtraction = parser.has_subtraction;
    pattern->count = compiler.count;
    if (compiler.count > schema->regex_max_states) {
        schema->regex_max_states = compiler.count;
    }
    pattern->next = schema->regex_patterns;
    schema->regex_patterns = pattern;
    return 0;
}

static int regex_cache_schema(th_schema *schema, th_node *node) {
    if (schema->kind == 0 && is_schema_el(schema, node, XSD_NS, "pattern")) {
        const th_node_attr *value = attr_exact(schema->tree, node, "value", 5);
        if (value != NULL) {
            if (regex_cache_add(schema, value->value, value->value_len) < 0) {
                return -1;
            }
        }
    } else if (schema->kind != 0 && is_schema_el(schema, node, RNG_NS, "param")) {
        const th_node_attr *name = attr_exact(schema->tree, node, "name", 4);
        if (name != NULL && u_eq_ascii(name->value, name->value_len, "pattern")) {
            Py_ssize_t len = 0;
            const Py_UCS4 *text = element_text_raw(schema->tree, node, &len);
            if (regex_cache_add(schema, text, len) < 0) {
                return -1;
            }
        }
    }
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT) {
            if (regex_cache_schema(schema, child) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM */
                return -1;                               /* GCOVR_EXCL_LINE */
            }
        } else if (is_chardata(child) && child->text_len > 0) {
            /* Lazy definitions must not realize shared schema spans during validation. */
            if (th_node_realize_text(schema->tree, child) == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                                           /* GCOVR_EXCL_LINE */
            }
        }
    }
    return 0;
}

typedef struct {
    const rstate **items;
    size_t len;
} rlist;

/* Match buffers for one validate() call, sized to the schema's largest pattern: two state lists, the split-walk stack
   and a per-state mark. The mark holds a generation that only grows, so no match has to clear it. */
typedef struct rscratch {
    const rstate **lists, **stack;
    size_t *visited;
    size_t gen;
} rscratch;

static rscratch *rx_scratch(th_schema *schema) {
    if (schema->regex_scratch == NULL) {
        const size_t states = schema->regex_max_states;
        rscratch *scratch = arena_alloc(&schema->mem, sizeof(rscratch));
        const rstate **lists = arena_alloc(&schema->mem, states * 3 * sizeof(rstate *));
        size_t *visited = arena_alloc(&schema->mem, states * sizeof(size_t));
        if (scratch == NULL || lists == NULL || visited == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM */
            return NULL;                                           /* GCOVR_EXCL_LINE */
        }
        memset(visited, 0, states * sizeof(size_t));
        *scratch = (rscratch){lists, lists + states * 2, visited, 0};
        schema->regex_scratch = scratch;
    }
    return schema->regex_scratch;
}

static void rx_push(rscratch *scratch, size_t *top, const rstate *state) {
    if (scratch->visited[state->index] != scratch->gen) {
        scratch->visited[state->index] = scratch->gen;
        scratch->stack[(*top)++] = state;
    }
}

/* Add every state reachable from start through split states. The iterative compiler builds long split chains, such as
   the one for a|a|a..., so walking them by recursion overflows a small thread stack; an explicit stack, as in RE2's
   NFA, holds at most one entry per state because a state is marked when pushed. */
static void rx_add(rlist *list, const rstate *start, rscratch *scratch) {
    size_t top = 0;
    rx_push(scratch, &top, start);
    while (top > 0) {
        const rstate *state = scratch->stack[--top];
        if (state->kind == RS_SPLIT) {
            rx_push(scratch, &top, state->out1);
            rx_push(scratch, &top, state->out);
        } else {
            list->items[list->len++] = state;
        }
    }
}

static Py_NO_INLINE rlist rx_match_subtracted(rscratch *scratch, rlist current, rlist next, const Py_UCS4 *value,
                                              Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        scratch->gen++;
        next.len = 0;
        for (size_t state = 0; state < current.len; state++) {
            const rstate *item = current.items[state];
            if ((item->kind == RS_MATCH && rx_state_match(item, value[index])) ||
                (item->kind == RS_SUB_MATCH && rx_class_match(item->cls, value[index]) &&
                 !rx_subtracted_match(item->cls->subtract, value[index]))) {
                rx_add(&next, item->out, scratch);
            }
        }
        rlist swap = current;
        current = next;
        next = swap;
    }
    return current;
}

static Py_NO_INLINE rlist rx_match_regular(rscratch *scratch, rlist current, rlist next, const Py_UCS4 *value,
                                           Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        scratch->gen++;
        next.len = 0;
        for (size_t state = 0; state < current.len; state++) {
            const rstate *item = current.items[state];
            if (item->kind == RS_MATCH && rx_state_match(item, value[index])) {
                rx_add(&next, item->out, scratch);
            }
        }
        rlist swap = current;
        current = next;
        next = swap;
    }
    return current;
}

static int regex_full_match(th_schema *schema, const Py_UCS4 *text, Py_ssize_t text_len, const Py_UCS4 *value,
                            Py_ssize_t len) {
    const rpattern *pattern = schema->regex_patterns;
    while (!u_eq_u(text, text_len, pattern->text, pattern->len)) {
        pattern = pattern->next;
    }
    rscratch *scratch = rx_scratch(schema);
    if (scratch == NULL) {           /* GCOVR_EXCL_BR_LINE: arena OOM */
        schema->regex_no_memory = 1; /* GCOVR_EXCL_LINE: validate() raises the MemoryError */
        return 0;                    /* GCOVR_EXCL_LINE: fail closed, the value does not match */
    }
    rlist current = {scratch->lists, 0};
    rlist next = {scratch->lists + schema->regex_max_states, 0};
    scratch->gen++;
    rx_add(&current, pattern->start, scratch);
    if (pattern->has_subtraction) {
        current = rx_match_subtracted(scratch, current, next, value, len);
    } else {
        current = rx_match_regular(scratch, current, next, value, len);
    }
    for (size_t state = 0; state < current.len; state++) {
        if (current.items[state]->kind == RS_ACCEPT) {
            return 1;
        }
    }
    return 0;
}

#endif /* TURBOHTML_VALIDATE_REGEX_H */
