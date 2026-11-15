/* A compact regular-expression matcher for the XSD `pattern` facet (and the RELAX NG
   equivalent). The pattern language is the XSD 1.0 regex subset: literals, `.`, escape
   classes (\d \D \w \W \s \S \i \I \c \C), character classes with ranges and negation, grouping,
   alternation, and the `? * + {m} {m,} {m,n}` quantifiers. It compiles to a Thompson
   NFA (Cox, "Regular Expression Matching Can Be Simple And Fast") and simulates it, so
   matching is linear in the input with no backtracking blow-up. Matching is anchored:
   the whole value must be consumed, as a facet requires. Included once into
   datatypes.h; every definition is static. */

#ifndef TURBOHTML_VALIDATE_REGEX_H
#define TURBOHTML_VALIDATE_REGEX_H

#include "core/work_steps.h"

#include "data/xsd_digit_table.h"
#include "data/xsd_category_table.h"
#include "data/xsd_block_table.h"
#include "data/xsd_word_table.h"

/* Bounds that keep an adversarial pattern from exhausting the C stack or memory. The quantifier compiler is iterative,
   so any repeat count is stack-safe and only the total NFA state count is bounded. Group nesting drives the recursive
   parser, so it takes the 250 levels PCRE2 (PARENS_NEST_LIMIT) and Rust's regex-syntax (nest_limit) allow for the
   same reason: a musl thread from uv's python-build-standalone gets a 130 KiB stack, which 1000 levels overflow. */
#define RX_MAX_GROUP_DEPTH TH_DEPTH_LIMIT(250)

/* Why a pattern failed to compile; each maps to its own error in regex_cache_add. */
enum {
    RX_FAIL_MEMORY = 1,
    RX_FAIL_NESTING,
    RX_FAIL_CLASS_NESTING,
    RX_FAIL_BOUND,
    RX_FAIL_STATES,
    RX_FAIL_PROPERTY,
    RX_FAIL_RANGE,
    RX_FAIL_CLASS,
    RX_FAIL_ESCAPE,
    RX_FAIL_GROUP,
    RX_FAIL_QUANTIFIER,
    RX_FAIL_BRACKET,
    RX_FAIL_STACK
};
/* Shared by all patterns of one schema: their NFAs stay in the schema arena for as long as the schema lives. */
#define RX_MAX_STATES 2000000
/* Matching steps every live NFA state once per character, and a counted repeat keeps many states live without
   lengthening the pattern. The state visits of one validate() call are therefore budgeted, as the XPath regex steps
   are: a base, plus a share for each character matched (see rx_share). */
#define RX_BUDGET_BASE 100000000
#define RX_BUDGET_PER_CHAR 1000
#define RX_CATEGORY_ALL ((UINT64_C(1) << 30) - 1)

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
    uint64_t category_mask;
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

enum { RS_SPLIT, RS_MATCH, RS_ACCEPT, RS_SUB_MATCH, RS_PROP_MATCH };

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
    int has_extended;
    Py_ssize_t fail_pos, fail_end; /* the span a nesting or bound error points at */
    int depth;                     /* open groups on the recursion path, capped at RX_MAX_GROUP_DEPTH */
    uintptr_t stack_floor;         /* the lowest frame address the parse may run at; see core/stack.h */
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

static int rx_range_order(const void *left, const void *right) {
    const Py_UCS4 first = ((const rrange *)left)->lo, second = ((const rrange *)right)->lo;
    return (first > second) - (first < second);
}

/* Sorted, disjoint ranges let rx_class_match bisect them; a class spelling many members would otherwise cost its
   length for every character it is matched against. */
static void rx_class_finish(rclass *cls) {
    if (cls->range_count < 2) {
        return;
    }
    qsort(cls->ranges, (size_t)cls->range_count, sizeof(rrange), rx_range_order);
    Py_ssize_t kept = 0;
    for (Py_ssize_t index = 1; index < cls->range_count; index++) {
        rrange *last = &cls->ranges[kept];
        const rrange *next = &cls->ranges[index];
        if (next->lo <= last->hi + 1) {
            if (next->hi > last->hi) {
                last->hi = next->hi;
            }
        } else {
            cls->ranges[++kept] = *next;
        }
    }
    cls->range_count = kept + 1;
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

static int rx_is_single_escape(Py_UCS4 letter) {
    return letter < 128 && strchr("nrt\\|.?*+(){}-[]^", (int)letter) != NULL;
}

static rnode *rx_new_class(rparser *parser) {
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
    return node;
}

static uint64_t rx_category_mask(const Py_UCS4 *name, Py_ssize_t len) {
    if (len == 2 && name[0] == 'C' && name[1] == 's') {
        return 0;
    }
    uint64_t mask = 0;
    for (size_t index = 0; index < 30; index++) {
        const char *category = TH_CATEGORY_NAMES[index];
        if ((len == 1 && name[0] == (Py_UCS4)category[0]) ||
            (len == 2 && name[0] == (Py_UCS4)category[0] && name[1] == (Py_UCS4)category[1])) {
            mask |= UINT64_C(1) << index;
        }
    }
    return mask;
}

static int rx_add_block(rparser *parser, rclass *cls, const Py_UCS4 *name, Py_ssize_t len, int complement) {
    if (len < 3 || name[0] != 'I' || name[1] != 's') {
        return 0;
    }
    uint32_t cursor = 0;
    int found = 0;
    for (size_t index = 0; index < sizeof(TH_XSD_BLOCKS) / sizeof(TH_XSD_BLOCKS[0]); index++) {
        const th_xsd_block *block = &TH_XSD_BLOCKS[index];
        const char *candidate = block->name;
        Py_ssize_t offset = 2;
        while (offset < len && candidate[offset - 2] != '\0' && name[offset] == (Py_UCS4)candidate[offset - 2]) {
            offset++;
        }
        if (offset != len || candidate[offset - 2] != '\0') {
            continue;
        }
        if (!complement) {
            if (rx_range_push(parser, cls, block->lo, block->hi) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM */
                return -1;                                              /* GCOVR_EXCL_LINE */
            }
        } else if (cursor < block->lo) {
            if (rx_range_push(parser, cls, cursor, block->lo - 1) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM */
                return -1;                                               /* GCOVR_EXCL_LINE */
            }
        }
        cursor = block->hi + 1;
        found = 1;
    }
    if (found && complement && cursor <= 0x10FFFF) {
        if (rx_range_push(parser, cls, cursor, 0x10FFFF) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM */
            return -1;                                          /* GCOVR_EXCL_LINE */
        }
    }
    return found;
}

static int rx_parse_property(rparser *parser, rclass *cls, Py_UCS4 letter) {
    Py_ssize_t start = parser->pos;
    if (start < parser->len && parser->pattern[start] == '{') {
        start++;
        parser->pos = start;
        while (parser->pos < parser->len && parser->pattern[parser->pos] != '}') {
            parser->pos++;
        }
        if (parser->pos < parser->len) {
            uint64_t mask = rx_category_mask(parser->pattern + start, parser->pos - start);
            if (mask != 0) {
                cls->category_mask |= letter == 'p' ? mask : RX_CATEGORY_ALL & ~mask;
                parser->pos++;
                parser->has_extended = 1;
                return 0;
            }
            int block = rx_add_block(parser, cls, parser->pattern + start, parser->pos - start, letter == 'P');
            if (block != 0) {
                if (block < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
                    return -1;   /* GCOVR_EXCL_LINE */
                }
                parser->pos++;
                return 0;
            }
        }
    }
    parser->failed = RX_FAIL_PROPERTY;
    parser->fail_pos = start;
    return -1;
}

static rnode *rx_parse_class(rparser *parser) {
    Py_ssize_t start = parser->pos - 1;
    rnode *node = rx_new_class(parser);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    rclass *cls = node->cls;
    if (parser->pos < parser->len && parser->pattern[parser->pos] == '^') {
        cls->negate = 1;
        parser->pos++;
    }
    while (parser->pos < parser->len && parser->pattern[parser->pos] != ']') {
        Py_UCS4 current = parser->pattern[parser->pos++];
        if (current == '-' && parser->pos < parser->len && parser->pattern[parser->pos] == '[' &&
            (cls->range_count > 0 || cls->builtins != 0 || cls->category_mask != 0)) {
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
            parser->has_extended = 1;
            break;
        }
        if (current == '[' || current == '^' ||
            (current == '-' && parser->pos < parser->len && parser->pattern[parser->pos] != ']' &&
             (cls->range_count > 0 || cls->builtins != 0 || cls->category_mask != 0))) {
            parser->failed = RX_FAIL_CLASS;
            parser->fail_pos = parser->pos - 1;
            return NULL;
        }
        if (current == '\\' && parser->pos < parser->len) {
            Py_UCS4 letter = parser->pattern[parser->pos++];
            if (letter == 'p' || letter == 'P') {
                if (rx_parse_property(parser, cls, letter) < 0) {
                    return NULL;
                }
                continue;
            }
            int flag = rx_builtin_flag(letter);
            if (flag != 0) {
                cls->builtins |= flag;
                continue;
            }
            if (!rx_is_single_escape(letter)) {
                parser->failed = RX_FAIL_ESCAPE;
                parser->fail_pos = parser->pos - 2;
                return NULL;
            }
            current = rx_escape_literal(letter);
        }
        if (parser->pos + 1 < parser->len && parser->pattern[parser->pos] == '-' &&
            parser->pattern[parser->pos + 1] != ']' && parser->pattern[parser->pos + 1] != '[') {
            Py_ssize_t range_pos = parser->pos - 1;
            Py_UCS4 hi = parser->pattern[parser->pos + 1];
            parser->pos += 2;
            if (hi == '\\') {
                if (parser->pos >= parser->len || !rx_is_single_escape(parser->pattern[parser->pos])) {
                    parser->failed = RX_FAIL_RANGE;
                    parser->fail_pos = range_pos;
                    return NULL;
                }
                hi = rx_escape_literal(parser->pattern[parser->pos++]);
            }
            if (hi < current) {
                parser->failed = RX_FAIL_RANGE;
                parser->fail_pos = range_pos;
                return NULL;
            }
            if (rx_range_push(parser, cls, current, hi) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
                return NULL;                                   /* GCOVR_EXCL_LINE */
            }
        } else if (rx_range_push(parser, cls, current, current) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM unforceable */
            return NULL;                                               /* GCOVR_EXCL_LINE */
        }
    }
    if (parser->pos >= parser->len || parser->pattern[parser->pos] != ']' ||
        (cls->range_count == 0 && cls->builtins == 0 && cls->category_mask == 0)) {
        parser->failed = RX_FAIL_CLASS;
        parser->fail_pos = start;
        return NULL;
    }
    rx_class_finish(cls);
    parser->pos++;
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
        Py_ssize_t start = parser->pos;
        if (parser->depth >= RX_MAX_GROUP_DEPTH) {
            parser->failed = RX_FAIL_NESTING;
            parser->fail_pos = parser->pos;
            return NULL;
        }
        parser->pos++;
        parser->depth++;
        rnode *inner = rx_parse_alt(parser);
        parser->depth--;
        if (inner == NULL) {
            return NULL;
        }
        if (parser->pos >= parser->len) {
            parser->failed = RX_FAIL_GROUP;
            parser->fail_pos = start;
            return NULL;
        }
        parser->pos++;
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
    if (current == '\\') {
        if (parser->pos + 1 >= parser->len) {
            parser->failed = RX_FAIL_ESCAPE;
            parser->fail_pos = parser->pos;
            return NULL;
        }
        Py_UCS4 letter = parser->pattern[parser->pos + 1];
        int flag = rx_builtin_flag(letter);
        parser->pos += 2;
        if (flag != 0) {
            return rx_builtin_node(parser, flag);
        }
        if (letter == 'p' || letter == 'P') {
            rnode *property = rx_new_class(parser);
            if (property == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
                return NULL;        /* GCOVR_EXCL_LINE */
            }
            if (rx_parse_property(parser, property->cls, letter) < 0) {
                return NULL;
            }
            rx_class_finish(property->cls);
            return property;
        }
        if (!rx_is_single_escape(letter)) {
            parser->failed = RX_FAIL_ESCAPE;
            parser->fail_pos = parser->pos - 2;
            return NULL;
        }
        rnode *node = rx_node(parser, RN_CHAR);
        if (node != NULL) { /* GCOVR_EXCL_BR_LINE: node is NULL only on unforceable arena OOM */
            node->ch = rx_escape_literal(letter);
        }
        return node;
    }
    if (current == '?' || current == '*' || current == '+' || current == '{' || current == '}') {
        parser->failed = RX_FAIL_QUANTIFIER;
        parser->fail_pos = parser->pos;
        return NULL;
    }
    if (current == ']') {
        parser->failed = RX_FAIL_BRACKET;
        parser->fail_pos = parser->pos;
        return NULL;
    }
    parser->pos++;
    rnode *node = rx_node(parser, RN_CHAR);
    if (node != NULL) { /* GCOVR_EXCL_BR_LINE: node is NULL only on unforceable arena OOM */
        node->ch = current;
    }
    return node;
}

/* Read a run of decimal digits as a quantifier count, saturating at the state budget: a larger count can never compile
   within it, and saturating keeps the arithmetic from overflowing. Returns the digit count. */
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

/* A piece that matches only the empty string compiles to no NFA state, so the state budget cannot bound the copies
   rx_compile_repeat makes of it, and nested repeats of such a piece multiply. Folding it here keeps every copy paid for
   by a state. x{1} folds to x so that a chain of single repeats is not walked once per level for every copy. */
static rnode *rx_parse_quant(rparser *parser) {
    rnode *atom = rx_parse_atom(parser);
    if (atom == NULL || parser->pos >= parser->len) { /* GCOVR_EXCL_BR_LINE: NULL only on unforceable arena OOM */
        return atom;
    }
    Py_UCS4 quant = parser->pattern[parser->pos];
    int type = quant == '?' ? RN_QUEST : (quant == '*' ? RN_STAR : (quant == '+' ? RN_PLUS : -1));
    if (type != -1) {
        parser->pos++;
        if (atom->type == RN_EMPTY) {
            return atom;
        }
        rnode *node = rx_node(parser, type);
        if (node != NULL) { /* GCOVR_EXCL_BR_LINE: node is NULL only on unforceable arena OOM */
            node->a = atom;
        }
        return node;
    }
    if (quant == '{') {
        int rmin, rmax;
        if (rx_parse_bound(parser, &rmin, &rmax)) {
            if (atom->type == RN_EMPTY || (rmin == 1 && rmax == 1)) {
                return atom;
            }
            if (rmax == 0) {
                return rx_node(parser, RN_EMPTY);
            }
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
        if (piece->type == RN_EMPTY) {
            continue; /* see rx_parse_quant */
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
    if (TH_FRAME_ADDRESS() < parser->stack_floor) {
        parser->failed = RX_FAIL_STACK;
        parser->fail_pos = parser->pos;
        return NULL;
    }
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
    size_t limit;
    int failed;
    rstate sink; /* absorbs writes once the pattern is refused, so no caller dereferences NULL */
} rcompiler;

static rstate *rx_state(rcompiler *compiler, int kind) {
    rstate *state = NULL;
    if (compiler->count >= compiler->limit) {
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
   growing the C stack; rx_state's budget bounds the memory, and the loops stop once it trips. Every copy moves towards
   it, because rx_parse_quant leaves no atom that compiles to nothing. An inverted bound such as {5,2} compiles as
   {5,}, as the recursive construction always did. */
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
        TH_WORK_STEP();
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
        int kind = RS_MATCH;
        if (node->type == RN_CLASS) {
            kind = node->cls->subtract != NULL     ? RS_SUB_MATCH
                   : node->cls->category_mask != 0 ? RS_PROP_MATCH
                                                   : RS_MATCH;
        }
        rstate *state = rx_state(compiler, kind);
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

static int rx_ranges_contain(const rclass *cls, Py_UCS4 codepoint) {
    Py_ssize_t low = 0, high = cls->range_count;
    while (low < high) {
        TH_WORK_STEP();
        const Py_ssize_t middle = low + (high - low) / 2;
        if (cls->ranges[middle].hi < codepoint) {
            low = middle + 1;
        } else {
            high = middle;
        }
    }
    return low < cls->range_count && cls->ranges[low].lo <= codepoint;
}

static int rx_class_match(const rclass *cls, Py_UCS4 codepoint) {
    int inside = rx_ranges_contain(cls, codepoint);
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

static int rx_property_match(const rclass *cls, Py_UCS4 codepoint) {
    uint16_t page = TH_CATEGORY_PAGE_KIND[codepoint / 256];
    uint8_t category = page < 30 ? (uint8_t)page : TH_CATEGORY_MIXED[page - 30][codepoint % 256];
    int in_property = (cls->category_mask & (UINT64_C(1) << category)) != 0;
    if (cls->range_count == 0 && cls->builtins == 0) {
        return cls->negate ? !in_property : in_property;
    }
    int in_class = rx_class_match(cls, codepoint);
    return cls->negate ? in_class && !in_property : in_class || in_property;
}

static int rx_subtracted_match(const rclass *cls, Py_UCS4 codepoint) {
    int matches = cls->category_mask != 0 ? rx_property_match(cls, codepoint) : rx_class_match(cls, codepoint);
    return matches && (cls->subtract == NULL || !rx_subtracted_match(cls->subtract, codepoint));
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
    int64_t share; /* state visits one character of a value adds to the budget, see rx_share */
    int has_extended;
    int duplicate; /* an earlier pattern of the schema has the same text and holds the compiled form */
} rpattern;

/* The state visits one character of a value adds to the budget: twice the states the pattern spells out, which covers
   a pattern without a counted repeat, plus RX_BUDGET_PER_CHAR for the copies a counted repeat keeps live. */
static int64_t rx_share(const rpattern *pattern) {
    const size_t spelled = pattern->count < (size_t)pattern->len + 1 ? pattern->count : (size_t)pattern->len + 1;
    return 2 * (int64_t)spelled + RX_BUDGET_PER_CHAR;
}

/* A total order on pattern texts. The schema's distinct patterns are sorted by it once, so looking one up by its text
   is a binary search: a linear scan made a type with P pattern facets cost P * P steps for every value it checked. */
static int rx_text_order(const Py_UCS4 *left, Py_ssize_t left_len, const Py_UCS4 *right, Py_ssize_t right_len) {
    if (left_len != right_len) {
        return left_len < right_len ? -1 : 1;
    }
    return left_len == 0 ? 0 : memcmp(left, right, (size_t)left_len * sizeof(Py_UCS4));
}

/* Equal texts order by position in regex_patterns, so each run of duplicates opens with the first in document order. */
static int rpattern_order(const void *left, const void *right) {
    const rpattern *first = *(const rpattern *const *)left;
    const rpattern *second = *(const rpattern *const *)right;
    const int order = rx_text_order(first->text, first->len, second->text, second->len);
    return order != 0 ? order : (first > second) - (first < second);
}

static int regex_cache_push(th_schema *schema, const Py_UCS4 *text, Py_ssize_t len) {
    if (schema->regex_count == schema->regex_cap) {
        size_t capacity, bytes;
        const int fits =
            th_grow_cap(schema->regex_count + 1, schema->regex_cap, 8, sizeof(rpattern), &capacity, &bytes);
        if (!fits) {          /* GCOVR_EXCL_BR_LINE: allocation size overflow */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        rpattern *patterns = arena_alloc(&schema->mem, bytes);
        if (patterns == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;          /* GCOVR_EXCL_LINE */
        }
        if (schema->regex_count > 0) {
            memcpy(patterns, schema->regex_patterns, schema->regex_count * sizeof(rpattern));
        }
        schema->regex_patterns = patterns;
        schema->regex_cap = capacity;
    }
    schema->regex_patterns[schema->regex_count++] = (rpattern){.text = text, .len = len};
    return 0;
}

static int regex_cache_add(th_schema *schema, rpattern *pattern) {
    const Py_UCS4 *text = pattern->text;
    const Py_ssize_t len = pattern->len;
#ifdef TH_STACK_FLOOR
    const uintptr_t floor = th_stack_floor();
#else
    const uintptr_t floor = 0;
#endif
    rparser parser = {.pattern = text, .len = len, .mem = &schema->mem, .stack_floor = floor};
    rnode *ast = rx_parse_alt(&parser);
    if (!parser.failed && parser.pos < len) {
        parser.failed = RX_FAIL_GROUP;
        parser.fail_pos = parser.pos;
    }
    rcompiler compiler = {.mem = &schema->mem, .limit = RX_MAX_STATES - schema->regex_states};
    rstate *start = parser.failed ? NULL : rx_compile(&compiler, ast, rx_state(&compiler, RS_ACCEPT));
    const int failed = parser.failed ? parser.failed : compiler.failed;
    if (failed == RX_FAIL_MEMORY) { /* GCOVR_EXCL_BR_LINE: arena OOM */
        PyErr_NoMemory();           /* GCOVR_EXCL_LINE */
        return -1;                  /* GCOVR_EXCL_LINE */
    }
    if (failed == RX_FAIL_STACK) {
        PyErr_SetString(PyExc_RecursionError,
                        "schema pattern nests groups deeper than this thread's C stack holds; run the compile on a "
                        "thread with a larger stack");
        return -1;
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
    if (failed == RX_FAIL_PROPERTY) {
        PyErr_Format(PyExc_ValueError, "schema pattern has an invalid category escape at offset %zd", parser.fail_pos);
        return -1;
    }
    if (failed == RX_FAIL_RANGE) {
        PyErr_Format(PyExc_ValueError, "schema pattern has an invalid character range at offset %zd", parser.fail_pos);
        return -1;
    }
    if (failed == RX_FAIL_CLASS) {
        PyErr_Format(PyExc_ValueError, "schema pattern has an invalid character class at offset %zd", parser.fail_pos);
        return -1;
    }
    if (failed == RX_FAIL_ESCAPE) {
        PyErr_Format(PyExc_ValueError, "schema pattern has an invalid escape at offset %zd", parser.fail_pos);
        return -1;
    }
    if (failed == RX_FAIL_GROUP) {
        PyErr_Format(PyExc_ValueError, "schema pattern has an unmatched parenthesis at offset %zd", parser.fail_pos);
        return -1;
    }
    if (failed == RX_FAIL_QUANTIFIER) {
        PyErr_Format(PyExc_ValueError, "schema pattern has an invalid quantifier at offset %zd", parser.fail_pos);
        return -1;
    }
    if (failed == RX_FAIL_BRACKET) {
        PyErr_Format(PyExc_ValueError, "schema pattern has an unmatched closing bracket at offset %zd",
                     parser.fail_pos);
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
    if (failed == RX_FAIL_STATES && schema->regex_states == 0) {
        PyErr_Format(PyExc_ValueError, "schema pattern needs more than %d NFA states; lower its repeat counts",
                     RX_MAX_STATES);
        return -1;
    }
    if (failed == RX_FAIL_STATES) {
        /* the pattern had only what the earlier ones left, so splitting one into several would not have helped */
        PyErr_Format(PyExc_ValueError,
                     "schema patterns need more than %d NFA states between them; lower their repeat counts",
                     RX_MAX_STATES);
        return -1;
    }
    pattern->start = start;
    pattern->has_extended = parser.has_extended;
    pattern->count = compiler.count;
    pattern->share = rx_share(pattern);
    schema->regex_states += compiler.count;
    if (compiler.count > schema->regex_max_states) {
        schema->regex_max_states = compiler.count;
    }
    return 0;
}

static int regex_cache_collect_at(th_schema *schema, th_node *node) {
    if (schema->kind == 0 && is_schema_el(schema, node, XSD_NS, "pattern")) {
        const th_node_attr *value = attr_exact(schema->tree, node, "value", 5);
        if (value != NULL) {
            if (regex_cache_push(schema, value->value, value->value_len) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM */
                return -1;                                                      /* GCOVR_EXCL_LINE */
            }
        }
    } else if (schema->kind != 0 && is_schema_el(schema, node, RNG_NS, "param")) {
        const th_node_attr *name = attr_exact(schema->tree, node, "name", 4);
        if (name != NULL && u_eq_ascii(name->value, name->value_len, "pattern")) {
            Py_ssize_t len = 0;
            const Py_UCS4 *text = element_text_raw(schema->tree, node, &len);
            if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
                return -1;          /* GCOVR_EXCL_LINE: allocation failure */
            }
            if (regex_cache_push(schema, text, len) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM */
                return -1;                                 /* GCOVR_EXCL_LINE */
            }
        }
    }
    return 0;
}

static int regex_cache_collect(th_schema *schema, th_node *root) {
    for (th_node *node = root; node != NULL; node = schema_walk_next(root, node)) {
        if (node->type == TH_NODE_ELEMENT) {
            if (regex_cache_collect_at(schema, node) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM */
                return -1;                                  /* GCOVR_EXCL_LINE */
            }
        } else if (is_chardata(node) && node->text_len > 0) {
            /* Lazy definitions must not realize shared schema spans during validation. */
            if (th_node_realize_text(schema->tree, node) == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                                          /* GCOVR_EXCL_LINE */
            }
        }
    }
    return 0;
}

/* Compile each distinct pattern of the schema once. The walk lists the patterns in document order; sorting pointers to
   them brings equal texts together, and the first of each run is compiled, still in document order so the schema's
   first invalid pattern is the one reported. The sorted pointers that remain are what regex_full_match searches. */
static int regex_cache_schema(th_schema *schema, th_node *node) {
    if (regex_cache_collect(schema, node) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM */
        return -1;                               /* GCOVR_EXCL_LINE */
    }
    if (schema->regex_count == 0) {
        return 0;
    }
    rpattern **sorted = arena_alloc(&schema->mem, schema->regex_count * sizeof(rpattern *));
    if (sorted == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    for (size_t index = 0; index < schema->regex_count; index++) {
        sorted[index] = &schema->regex_patterns[index];
    }
    qsort(sorted, schema->regex_count, sizeof(rpattern *), rpattern_order);
    size_t distinct = 1;
    for (size_t index = 1; index < schema->regex_count; index++) {
        const rpattern *kept = sorted[distinct - 1];
        if (rx_text_order(kept->text, kept->len, sorted[index]->text, sorted[index]->len) == 0) {
            sorted[index]->duplicate = 1;
        } else {
            sorted[distinct++] = sorted[index];
        }
    }
    schema->regex_sorted = sorted;
    schema->regex_distinct = distinct;
    for (size_t index = 0; index < schema->regex_count; index++) {
        if (schema->regex_patterns[index].duplicate) {
            continue;
        }
        TH_WORK_STEP();
        if (regex_cache_add(schema, &schema->regex_patterns[index]) < 0) {
            return -1;
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
    int64_t budget; /* state visits the call may still spend; negative once a match ran out of them */
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
        *scratch = (rscratch){lists, lists + states * 2, visited, 0, RX_BUDGET_BASE};
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
   NFA, holds at most one entry per state because a state is marked when pushed. The states walked are charged to the
   budget. */
static void rx_add(rlist *list, const rstate *start, rscratch *scratch) {
    size_t top = 0, walked = 0;
    rx_push(scratch, &top, start);
    while (top > 0) {
        const rstate *state = scratch->stack[--top];
        walked++;
        if (state->kind == RS_SPLIT) {
            rx_push(scratch, &top, state->out1);
            rx_push(scratch, &top, state->out);
        } else {
            list->items[list->len++] = state;
        }
    }
    scratch->budget -= (int64_t)walked;
}

static Py_NO_INLINE rlist rx_match_extended(rscratch *scratch, rlist current, rlist next, const Py_UCS4 *value,
                                            Py_ssize_t len) {
    /* each state a character steps was reached by a walk that rx_add already charged, so checking the budget per
       character bounds the match */
    for (Py_ssize_t index = 0; index < len && scratch->budget >= 0; index++) {
        scratch->gen++;
        next.len = 0;
        for (size_t state = 0; state < current.len; state++) {
            const rstate *item = current.items[state];
            if ((item->kind == RS_MATCH && rx_state_match(item, value[index])) ||
                (item->kind == RS_PROP_MATCH && rx_property_match(item->cls, value[index])) ||
                (item->kind == RS_SUB_MATCH &&
                 (item->cls->category_mask != 0 ? rx_property_match(item->cls, value[index])
                                                : rx_class_match(item->cls, value[index])) &&
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
    for (Py_ssize_t index = 0; index < len && scratch->budget >= 0; index++) { /* as in rx_match_extended */
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

static const rpattern *regex_cache_find(const th_schema *schema, const Py_UCS4 *text, Py_ssize_t len) {
    size_t low = 0, high = schema->regex_distinct;
    while (low < high) { /* GCOVR_EXCL_BR_LINE: the pattern is always present, so the loop exits by return */
        const size_t middle = low + (high - low) / 2;
        const rpattern *pattern = schema->regex_sorted[middle];
        TH_WORK_STEP();
        const int order = rx_text_order(pattern->text, pattern->len, text, len);
        if (order == 0) {
            return pattern;
        }
        if (order < 0) {
            low = middle + 1;
        } else {
            high = middle;
        }
    }
    return NULL; /* GCOVR_EXCL_LINE: regex_cache_schema compiled every facet's pattern, so the search never misses */
}

/* Whether a pattern check of this validate() call ran out of budget. Its mismatch is then no verdict, so the entry
   raises instead of reporting it. */
static int regex_exhausted(const th_schema *schema) {
    return schema->regex_scratch != NULL && schema->regex_scratch->budget < 0;
}

static int regex_full_match(th_schema *schema, const Py_UCS4 *text, Py_ssize_t text_len, const Py_UCS4 *value,
                            Py_ssize_t len) {
    const rpattern *pattern = regex_cache_find(schema, text, text_len);
    rscratch *scratch = rx_scratch(schema);
    if (scratch == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM; validate() raises the MemoryError */
        return 0;          /* GCOVR_EXCL_LINE: fail closed, the value does not match */
    }
    if (scratch->budget < 0) {
        return 0; /* the call already fails, so the rest of the document is not worth matching */
    }
    scratch->budget += ((int64_t)len + 1) * pattern->share;
    rlist current = {scratch->lists, 0};
    rlist next = {scratch->lists + schema->regex_max_states, 0};
    scratch->gen++;
    rx_add(&current, pattern->start, scratch);
    if (pattern->has_extended) {
        current = rx_match_extended(scratch, current, next, value, len);
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
