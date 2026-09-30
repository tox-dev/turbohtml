/* The parser follows CPython's re/_parser.py so a pattern means here what it means under re; both matchers interpret
   the one instruction set the compiler emits. */

#include "query/xpath/regex.h"

#include "core/ascii.h"
#include "core/pycompat.h"
#include "core/vec.h"

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#ifndef TH_NOINLINE
#if defined(_MSC_VER)
#define TH_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define TH_NOINLINE __attribute__((noinline))
#else
#define TH_NOINLINE
#endif
#endif

#define XR_CACHE_SLOTS 64
/* What the group parsers return besides a node index or -1 for an error: a construct that adds no item, and an opening
   whose body the caller parses next. */
#define XR_NO_ITEM (-2)
#define XR_GROUP_BODY (-3)

enum { XRN_EMPTY, XRN_CHAR, XRN_ANY, XRN_CLASS, XRN_ASSERT, XRN_BACKREF, XRN_GROUP, XRN_CONCAT, XRN_ALT, XRN_REPEAT };

/* Consuming instructions come first: op <= XRI_CLASS reads exactly one character, op <= XRI_BACKREF_FOLD consumes
   any. */
enum {
    XRI_CHAR,
    XRI_FOLD,
    XRI_ANY,
    XRI_ANY_NL,
    XRI_CLASS,
    XRI_BACKREF,
    XRI_BACKREF_FOLD,
    XRI_MATCH,
    XRI_SPLIT,
    XRI_JMP,
    XRI_SAVE,
    XRI_MARK,
    XRI_CHECK,
    XRI_ASSERT,
};

enum { XRA_START, XRA_END, XRA_EOL, XRA_MBOL, XRA_MEOL, XRA_WORD, XRA_NOT_WORD };

enum { XRC_DIGIT = 1, XRC_NOT_DIGIT = 2, XRC_WORD = 4, XRC_NOT_WORD = 8, XRC_SPACE = 16, XRC_NOT_SPACE = 32 };

typedef struct {
    uint8_t kind;
    uint8_t flag; /* case folding (char, class, backref), dot-all (any), greedy (repeat) */
    uint8_t nullable;
    int32_t value; /* code point, class, assertion, or group (-1 when not capturing) */
    int32_t child;
    int32_t next;
    int32_t min;
    int32_t max; /* -1 when unbounded */
} xr_node;

typedef struct {
    Py_UCS4 lo, hi;
} xr_range;

typedef struct {
    int32_t first;
    int32_t count;
    uint8_t categories;
    uint8_t negate;
    uint8_t fold;
    uint32_t ascii[4];
} xr_class;

/* next is where execution continues (the target of a JMP and of a CHECK that fires), alt the lower-priority branch of a
   SPLIT. */
typedef struct {
    uint8_t op;
    int32_t arg;
    int32_t next;
    int32_t alt;
} xr_inst;

typedef struct {
    Py_ssize_t offset;
    Py_ssize_t len;
    int32_t group;
} xr_name;

/* A pending Pike VM branch or backtracking choice (slot -1), or a capture slot to restore to value. */
typedef struct {
    int32_t pc;
    int32_t slot;
    Py_ssize_t value;
} xr_frame;

typedef struct {
    int32_t *dense;
    int32_t *sparse;
    Py_ssize_t *slots;
    int32_t len;
} xr_list;

/* Rust regex's default size_limit, applied to each growable buffer; capping the program bounds a Pike VM step too,
   which visits each instruction once, against a counted repeat such as a{1000}{1000}. */
#define XR_MAX_BYTES ((size_t)10 << 20)
#define XR_MAX_CODE ((int32_t)(XR_MAX_BYTES / sizeof(xr_inst)))
#define XR_MAX_TRAIL ((Py_ssize_t)(XR_MAX_BYTES / sizeof(xr_frame)))
/* PCRE2's default match_limit, plus a per-character share so a long text is not starved: 100 is five times the 18 a
   global replace of (\w+)\W+\1 over prose spends, the heaviest common pattern measured. */
#define XR_BUDGET_BASE 10000000
#define XR_BUDGET_PER_CHAR 100

typedef struct {
    Py_UCS4 *pattern;
    Py_ssize_t pattern_len;
    int flags;
    xr_inst *code;
    int32_t code_count;
    xr_class *classes;
    xr_range *ranges;
    xr_name *names;
    int32_t name_count;
    int32_t groups;
    int32_t slots;     /* capture slots, 2 per group plus the whole match */
    int32_t registers; /* loop-start positions only the backtracker reads */
    int32_t prefilter; /* the first consuming instruction when every match starts with it */
    int32_t prefix;    /* how many literal characters every match starts with */
    uint8_t anchored;
    uint8_t backrefs;
    xr_list lists[2];
    int32_t *states;   /* backs both lists' dense and sparse arrays */
    Py_ssize_t *cells; /* backs both lists' capture slots */
    xr_frame *frames;
    Py_ssize_t *scratch;
    Py_ssize_t *best;
    xr_frame *trail;
    Py_ssize_t trail_cap;
} xr_program;

struct xr_cache {
    xr_program *programs[XR_CACHE_SLOTS];
    Py_ssize_t allowance; /* backtracking steps granted so far in the evaluation */
    Py_ssize_t budget;    /* the part of the allowance still unspent */
};

typedef struct {
    Py_UCS4 key, value;
} xr_pair;

/* Lowercase letters re's case-insensitive matching treats as one (re/_casefix.py), each mapped to the smallest member
   of its set. Sorted by key. */
static const xr_pair FOLD_ALIAS[] = {
    {0x0131, 0x0069}, {0x017F, 0x0073}, {0x03B9, 0x0345}, {0x03BC, 0x00B5}, {0x03C3, 0x03C2}, {0x03D0, 0x03B2},
    {0x03D1, 0x03B8}, {0x03D5, 0x03C6}, {0x03D6, 0x03C0}, {0x03F0, 0x03BA}, {0x03F1, 0x03C1}, {0x03F5, 0x03B5},
    {0x1C80, 0x0432}, {0x1C81, 0x0434}, {0x1C82, 0x043E}, {0x1C83, 0x0441}, {0x1C84, 0x0442}, {0x1C85, 0x0442},
    {0x1C86, 0x044A}, {0x1C87, 0x0463}, {0x1E9B, 0x1E61}, {0x1FBE, 0x0345}, {0x1FD3, 0x0390}, {0x1FE3, 0x03B0},
    {0xA64B, 0x1C88}, {0xFB06, 0xFB05},
};

/* Every code point whose fold is key without being key or key's uppercase: the extra members a case-insensitive class
   must look up. Sorted by key. */
static const xr_pair FOLD_EXTRA[] = {
    {0x0069, 0x0130}, {0x0069, 0x0131}, {0x006B, 0x212A}, {0x0073, 0x017F}, {0x00B5, 0x03BC}, {0x00DF, 0x1E9E},
    {0x00E5, 0x212B}, {0x01C6, 0x01C5}, {0x01C9, 0x01C8}, {0x01CC, 0x01CB}, {0x01F3, 0x01F2}, {0x0345, 0x03B9},
    {0x0345, 0x1FBE}, {0x0390, 0x1FD3}, {0x03B0, 0x1FE3}, {0x03B2, 0x03D0}, {0x03B5, 0x03F5}, {0x03B8, 0x03D1},
    {0x03B8, 0x03F4}, {0x03BA, 0x03F0}, {0x03C0, 0x03D6}, {0x03C1, 0x03F1}, {0x03C2, 0x03C3}, {0x03C6, 0x03D5},
    {0x03C9, 0x2126}, {0x0432, 0x1C80}, {0x0434, 0x1C81}, {0x043E, 0x1C82}, {0x0441, 0x1C83}, {0x0442, 0x1C84},
    {0x0442, 0x1C85}, {0x044A, 0x1C86}, {0x0463, 0x1C87}, {0x1C88, 0xA64B}, {0x1E61, 0x1E9B}, {0x1F80, 0x1F88},
    {0x1F81, 0x1F89}, {0x1F82, 0x1F8A}, {0x1F83, 0x1F8B}, {0x1F84, 0x1F8C}, {0x1F85, 0x1F8D}, {0x1F86, 0x1F8E},
    {0x1F87, 0x1F8F}, {0x1F90, 0x1F98}, {0x1F91, 0x1F99}, {0x1F92, 0x1F9A}, {0x1F93, 0x1F9B}, {0x1F94, 0x1F9C},
    {0x1F95, 0x1F9D}, {0x1F96, 0x1F9E}, {0x1F97, 0x1F9F}, {0x1FA0, 0x1FA8}, {0x1FA1, 0x1FA9}, {0x1FA2, 0x1FAA},
    {0x1FA3, 0x1FAB}, {0x1FA4, 0x1FAC}, {0x1FA5, 0x1FAD}, {0x1FA6, 0x1FAE}, {0x1FA7, 0x1FAF}, {0x1FB3, 0x1FBC},
    {0x1FC3, 0x1FCC}, {0x1FF3, 0x1FFC}, {0xFB05, 0xFB06},
};

#define XR_COUNT(table) (sizeof(table) / sizeof((table)[0]))

static size_t xr_lower_bound(const xr_pair *pairs, size_t count, Py_UCS4 key) {
    size_t low = 0;
    while (count > 0) {
        size_t half = count / 2;
        if (pairs[low + half].key < key) {
            low += half + 1;
            count -= half + 1;
        } else {
            count = half;
        }
    }
    return low;
}

static Py_UCS4 xr_lower(Py_UCS4 ch) {
    return ch < 128 ? lower_ascii(ch) : Py_UNICODE_TOLOWER(ch);
}

/* The representative re compares case-insensitively: the lowercase form, with the letters that share an uppercase
   collapsed onto one member. */
static Py_UCS4 xr_fold(Py_UCS4 ch) {
    Py_UCS4 lowered = xr_lower(ch);
    size_t index = xr_lower_bound(FOLD_ALIAS, XR_COUNT(FOLD_ALIAS), lowered);
    return index < XR_COUNT(FOLD_ALIAS) && FOLD_ALIAS[index].key == lowered ? FOLD_ALIAS[index].value : lowered;
}

static int xr_is_word(Py_UCS4 ch) {
    return ch == '_' || Py_UNICODE_ISALNUM(ch);
}

static int xr_category(int categories, Py_UCS4 ch) {
    int present = (Py_UNICODE_ISDECIMAL(ch) ? XRC_DIGIT : XRC_NOT_DIGIT) | (xr_is_word(ch) ? XRC_WORD : XRC_NOT_WORD) |
                  (Py_UNICODE_ISSPACE(ch) ? XRC_SPACE : XRC_NOT_SPACE);
    return (categories & present) != 0;
}

static int xr_ranges_contain(const xr_range *ranges, int32_t count, Py_UCS4 ch) {
    int32_t low = 0;
    int32_t remaining = count;
    while (remaining > 0) {
        int32_t half = remaining / 2;
        if (ranges[low + half].hi < ch) {
            low += half + 1;
            remaining -= half + 1;
        } else {
            remaining = half;
        }
    }
    return low < count && ranges[low].lo <= ch;
}

/* re folds a case-insensitive class by testing the input's fold against the folds of its members; the members sharing a
   fold are the fold, its uppercase and FOLD_EXTRA. */
static int xr_class_slow(const xr_class *cls, const xr_range *ranges, Py_UCS4 ch) {
    const xr_range *own = ranges + cls->first;
    int inside;
    if (cls->fold) {
        Py_UCS4 folded = xr_fold(ch);
        Py_UCS4 upper = Py_UNICODE_TOUPPER(folded);
        inside = xr_category(cls->categories, xr_lower(ch)) || xr_ranges_contain(own, cls->count, folded) ||
                 (xr_fold(upper) == folded && xr_ranges_contain(own, cls->count, upper));
        size_t index = xr_lower_bound(FOLD_EXTRA, XR_COUNT(FOLD_EXTRA), folded);
        while (!inside && index < XR_COUNT(FOLD_EXTRA) && FOLD_EXTRA[index].key == folded) {
            inside = xr_ranges_contain(own, cls->count, FOLD_EXTRA[index++].value);
        }
    } else {
        inside = xr_category(cls->categories, ch) || xr_ranges_contain(own, cls->count, ch);
    }
    return inside != cls->negate;
}

static int xr_class_match(const xr_class *cls, const xr_range *ranges, Py_UCS4 ch) {
    if (ch < 128) {
        return (int)((cls->ascii[ch >> 5] >> (ch & 31)) & 1);
    }
    return xr_class_slow(cls, ranges, ch);
}

static int xr_consume(const xr_program *program, const xr_inst *inst, Py_UCS4 ch) {
    switch (inst->op) {
    case XRI_CHAR:
        return ch == (Py_UCS4)inst->arg;
    case XRI_FOLD:
        return xr_fold(ch) == (Py_UCS4)inst->arg;
    case XRI_ANY:
        return 1;
    case XRI_ANY_NL:
        return ch != '\n';
    case XRI_CLASS:
        return xr_class_match(&program->classes[inst->arg], program->ranges, ch);
    default:
        return 0;
    }
}

static int xr_assert(int kind, const Py_UCS4 *text, Py_ssize_t len, Py_ssize_t pos) {
    switch (kind) {
    case XRA_START:
        return pos == 0;
    case XRA_END:
        return pos == len;
    case XRA_EOL:
        return pos == len || (pos == len - 1 && text[pos] == '\n');
    case XRA_MBOL:
        return pos == 0 || text[pos - 1] == '\n';
    case XRA_MEOL:
        return pos == len || text[pos] == '\n';
    default: {
        /* the empty string has no word boundary, so \B matches there (as in Python 3.14) */
        int boundary = len > 0 && (pos > 0 && xr_is_word(text[pos - 1])) != (pos < len && xr_is_word(text[pos]));
        return boundary == (kind == XRA_WORD);
    }
    }
}

static int xr_grow(void **items, int32_t *cap, int32_t needed, size_t size) {
    if (needed <= *cap) {
        return 0;
    }
    size_t new_cap, bytes;
    /* GCOVR_EXCL_BR_START: size overflow */
    if (!th_grow_cap((size_t)needed, (size_t)*cap, 16, size, &new_cap, &bytes) || new_cap > INT32_MAX) {
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: size-overflow path */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    void *grown = PyMem_Realloc(*items, bytes);
    if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    *items = grown;
    *cap = (int32_t)new_cap;
    return 0;
}

/* A group whose ')' the parser has not reached: its open branch, the alternation its finished branches form, and what
   closing it restores. */
typedef struct {
    int32_t head;
    int32_t tail;
    int32_t alternation;
    int32_t branch;
    int32_t group; /* capture number, -1 when not capturing */
    int flags;     /* the flags outside the group */
    Py_ssize_t start;
} xr_level;

typedef struct {
    const Py_UCS4 *pattern;
    Py_ssize_t len;
    Py_ssize_t pos;
    int flags;
    xr_level *levels;
    int32_t level_count, level_cap;
    xr_node *nodes;
    int32_t node_count, node_cap;
    xr_class *classes;
    int32_t class_count, class_cap;
    xr_range *ranges;
    int32_t range_count, range_cap;
    uint8_t *closed;
    int32_t groups, closed_cap;
    xr_name *names;
    int32_t name_count, name_cap;
    int backrefs;
    xr_error *error;
} xr_parser;

static int32_t xr_fail(xr_parser *parser, const char *message, Py_ssize_t position) {
    parser->error->pattern_error = 1;
    PyOS_snprintf(parser->error->message, sizeof(parser->error->message), "%s at position %lld", message,
                  (long long)position);
    return -1;
}

static int32_t xr_unsupported(const char *construct, const char *remedy) {
    PyErr_Format(PyExc_ValueError, "unsupported regular expression syntax: %s; %s", construct, remedy);
    return -1;
}

static int32_t xr_node_new(xr_parser *parser, int kind) {
    /* GCOVR_EXCL_BR_START: allocation failure */
    if (xr_grow((void **)&parser->nodes, &parser->node_cap, parser->node_count + 1, sizeof(xr_node)) < 0) {
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* GCOVR_EXCL_BR_STOP */
    xr_node *node = &parser->nodes[parser->node_count];
    memset(node, 0, sizeof(*node));
    node->kind = (uint8_t)kind;
    node->child = -1;
    node->next = -1;
    node->max = -1;
    node->nullable = (uint8_t)(kind == XRN_EMPTY || kind == XRN_ASSERT || kind == XRN_BACKREF);
    return parser->node_count++;
}

static int32_t xr_leaf(xr_parser *parser, int kind, int32_t value, int flag) {
    int32_t index = xr_node_new(parser, kind);
    if (index >= 0) { /* GCOVR_EXCL_BR_LINE: negative only on allocation failure */
        parser->nodes[index].value = value;
        parser->nodes[index].flag = (uint8_t)flag;
    }
    return index;
}

static int32_t xr_char_node(xr_parser *parser, Py_UCS4 ch) {
    return xr_leaf(parser, XRN_CHAR, (int32_t)ch, (parser->flags & XR_IGNORECASE) != 0);
}

static int xr_class_add(xr_parser *parser, Py_UCS4 lo, Py_UCS4 hi) {
    /* GCOVR_EXCL_BR_START: allocation failure */
    if (xr_grow((void **)&parser->ranges, &parser->range_cap, parser->range_count + 1, sizeof(xr_range)) < 0) {
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* GCOVR_EXCL_BR_STOP */
    parser->ranges[parser->range_count].lo = lo;
    parser->ranges[parser->range_count].hi = hi;
    parser->range_count++;
    parser->classes[parser->class_count - 1].count++;
    return 0;
}

static int32_t xr_class_new(xr_parser *parser, int categories, int fold) {
    /* GCOVR_EXCL_BR_START: allocation failure */
    if (xr_grow((void **)&parser->classes, &parser->class_cap, parser->class_count + 1, sizeof(xr_class)) < 0) {
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* GCOVR_EXCL_BR_STOP */
    xr_class *cls = &parser->classes[parser->class_count];
    memset(cls, 0, sizeof(*cls));
    cls->first = parser->range_count;
    cls->categories = (uint8_t)categories;
    cls->fold = (uint8_t)fold;
    return parser->class_count++;
}

static int xr_range_order(const void *left, const void *right) {
    const xr_range *first = left;
    const xr_range *second = right;
    return (first->lo > second->lo) - (first->lo < second->lo);
}

/* Sort and merge the class's ranges for binary search, then precompute the ASCII answers so the common case is one bit
   test. */
static int32_t xr_class_finish(xr_parser *parser, int32_t index) {
    xr_class *cls = &parser->classes[index];
    xr_range *own = parser->ranges + cls->first;
    qsort(own, (size_t)cls->count, sizeof(xr_range), xr_range_order);
    int32_t merged = 0;
    for (int32_t item = 0; item < cls->count; item++) {
        if (merged > 0 && own[item].lo <= own[merged - 1].hi + 1) {
            own[merged - 1].hi = own[item].hi > own[merged - 1].hi ? own[item].hi : own[merged - 1].hi;
        } else {
            own[merged++] = own[item];
        }
    }
    parser->range_count = cls->first + merged;
    cls->count = merged;
    for (Py_UCS4 ch = 0; ch < 128; ch++) {
        cls->ascii[ch >> 5] |= (uint32_t)xr_class_slow(cls, parser->ranges, ch) << (ch & 31);
    }
    return xr_leaf(parser, XRN_CLASS, index, 0);
}

static int32_t xr_category_node(xr_parser *parser, int categories) {
    int32_t index = xr_class_new(parser, categories, 0);
    /* GCOVR_EXCL_BR_START: negative only on allocation failure */
    return index < 0 ? -1 : xr_class_finish(parser, index);
    /* GCOVR_EXCL_BR_STOP */
}

static int xr_escape_category(Py_UCS4 letter) {
    switch (letter) {
    case 'd':
        return XRC_DIGIT;
    case 'D':
        return XRC_NOT_DIGIT;
    case 'w':
        return XRC_WORD;
    case 'W':
        return XRC_NOT_WORD;
    case 's':
        return XRC_SPACE;
    case 'S':
        return XRC_NOT_SPACE;
    default:
        return 0;
    }
}

static int xr_is_octal(const xr_parser *parser, Py_ssize_t pos) {
    return pos < parser->len && parser->pattern[pos] >= '0' && parser->pattern[pos] <= '7';
}

/* The value of an octal escape after its consumed first digit, taking up to two more digits. -1 when it exceeds
   0o377, which re rejects. */
static int64_t xr_octal(xr_parser *parser, Py_UCS4 first, Py_ssize_t start) {
    int64_t value = first - '0';
    for (int extra = 0; extra < 2 && xr_is_octal(parser, parser->pos); extra++) {
        value = value * 8 + (parser->pattern[parser->pos++] - '0');
    }
    return value > 0377 ? xr_fail(parser, "octal escape value outside of range 0-0o377", start) : value;
}

static int64_t xr_hex(xr_parser *parser, int digits, Py_ssize_t start) {
    int64_t value = 0;
    for (int index = 0; index < digits; index++) {
        Py_UCS4 ch = parser->pos < parser->len ? parser->pattern[parser->pos] : 0;
        if (!is_ascii_hexdigit(ch)) {
            return xr_fail(parser, "incomplete escape", start);
        }
        value = value * 16 + (is_ascii_digit(ch) ? ch - '0' : (ch | 0x20) - 'a' + 10);
        parser->pos++;
    }
    return value > 0x10FFFF ? xr_fail(parser, "bad escape", start) : value;
}

/* The literal an escape stands for, shared by classes and the pattern body once each has handled its own letters: -1 on
   error. */
static int64_t xr_escape_literal(xr_parser *parser, Py_UCS4 letter, Py_ssize_t start) {
    switch (letter) {
    case '0':
        return xr_octal(parser, letter, start);
    case 'a':
        return 7;
    case 'f':
        return 12;
    case 'n':
        return 10;
    case 'r':
        return 13;
    case 't':
        return 9;
    case 'v':
        return 11;
    case 'x':
        return xr_hex(parser, 2, start);
    case 'u':
        return xr_hex(parser, 4, start);
    case 'U':
        return xr_hex(parser, 8, start);
    case 'N':
        return xr_unsupported("named Unicode escapes \\N{...}", "write the character itself or \\uXXXX");
    default:
        if (is_ascii_alpha(letter) || is_ascii_digit(letter)) {
            return xr_fail(parser, "bad escape", start);
        }
        return letter;
    }
}

static int32_t xr_backref_node(xr_parser *parser, int32_t group, Py_ssize_t start) {
    if (group > parser->groups) {
        char message[64];
        PyOS_snprintf(message, sizeof(message), "invalid group reference %d", (int)group);
        return xr_fail(parser, message, start);
    }
    if (!parser->closed[group]) {
        return xr_fail(parser, "cannot refer to an open group", start);
    }
    parser->backrefs = 1;
    return xr_leaf(parser, XRN_BACKREF, group, (parser->flags & XR_IGNORECASE) != 0);
}

/* \1 to \99 name a group, unless three octal digits spell a character. */
static int32_t xr_group_reference(xr_parser *parser, Py_UCS4 letter, Py_ssize_t start) {
    int32_t group = (int32_t)(letter - '0');
    if (parser->pos < parser->len && is_ascii_digit(parser->pattern[parser->pos])) {
        Py_UCS4 second = parser->pattern[parser->pos++];
        if (letter <= '7' && second <= '7' && xr_is_octal(parser, parser->pos)) {
            Py_UCS4 third = parser->pattern[parser->pos++];
            int32_t value = (int32_t)((letter - '0') * 64 + (second - '0') * 8 + (third - '0'));
            return value > 0377 ? xr_fail(parser, "octal escape value outside of range 0-0o377", start)
                                : xr_char_node(parser, (Py_UCS4)value);
        }
        group = group * 10 + (int32_t)(second - '0');
    }
    return xr_backref_node(parser, group, start);
}

static int32_t xr_parse_escape(xr_parser *parser) {
    Py_ssize_t start = parser->pos;
    if (start + 1 >= parser->len) {
        return xr_fail(parser, "bad escape (end of pattern)", start);
    }
    Py_UCS4 letter = parser->pattern[start + 1];
    parser->pos = start + 2;
    int category = xr_escape_category(letter);
    if (category != 0) {
        return xr_category_node(parser, category);
    }
    switch (letter) {
    case 'A':
        return xr_leaf(parser, XRN_ASSERT, XRA_START, 0);
    case 'Z':
    case 'z':
        return xr_leaf(parser, XRN_ASSERT, XRA_END, 0);
    case 'b':
        return xr_leaf(parser, XRN_ASSERT, XRA_WORD, 0);
    case 'B':
        return xr_leaf(parser, XRN_ASSERT, XRA_NOT_WORD, 0);
    default:
        break;
    }
    if (letter >= '1' && letter <= '9') {
        return xr_group_reference(parser, letter, start);
    }
    int64_t value = xr_escape_literal(parser, letter, start);
    return value < 0 ? -1 : xr_char_node(parser, (Py_UCS4)value);
}

/* One class member: a code point, -2 for a category (its bits in *category), -1 on error. */
static int64_t xr_class_item(xr_parser *parser, int *category) {
    Py_ssize_t start = parser->pos;
    Py_UCS4 current = parser->pattern[parser->pos++];
    if (current != '\\') {
        return current;
    }
    if (parser->pos >= parser->len) {
        return xr_fail(parser, "unterminated character set", start);
    }
    Py_UCS4 letter = parser->pattern[parser->pos++];
    *category = xr_escape_category(letter);
    if (*category != 0) {
        return -2;
    }
    if (letter == 'b') {
        return 8;
    }
    if (letter >= '1' && letter <= '7') {
        return xr_octal(parser, letter, start);
    }
    return xr_escape_literal(parser, letter, start);
}

static int32_t xr_parse_class(xr_parser *parser) {
    Py_ssize_t start = parser->pos++;
    int32_t index = xr_class_new(parser, 0, (parser->flags & XR_IGNORECASE) != 0);
    if (index < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (parser->pos < parser->len && parser->pattern[parser->pos] == '^') {
        parser->classes[index].negate = 1;
        parser->pos++;
    }
    Py_ssize_t first = parser->pos;
    for (;;) {
        if (parser->pos >= parser->len) {
            return xr_fail(parser, "unterminated character set", start);
        }
        if (parser->pattern[parser->pos] == ']' && parser->pos > first) {
            parser->pos++;
            return xr_class_finish(parser, index);
        }
        Py_ssize_t item = parser->pos;
        int category = 0;
        int64_t low = xr_class_item(parser, &category);
        if (low == -1) {
            return -1;
        }
        if (parser->pos + 1 < parser->len && parser->pattern[parser->pos] == '-' &&
            parser->pattern[parser->pos + 1] != ']') {
            parser->pos++;
            int64_t high = xr_class_item(parser, &category);
            if (high == -1) {
                return -1;
            }
            if (low < 0 || high < low) {
                return xr_fail(parser, "bad character range", item);
            }
            if (xr_class_add(parser, (Py_UCS4)low, (Py_UCS4)high) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                                               /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        } else if (low < 0) {
            parser->classes[index].categories |= (uint8_t)category;
        } else if (xr_class_add(parser, (Py_UCS4)low, (Py_UCS4)low) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
}

static int xr_name_equal(const xr_parser *parser, const xr_name *name, Py_ssize_t offset, Py_ssize_t len) {
    return name->len == len &&
           memcmp(parser->pattern + name->offset, parser->pattern + offset, (size_t)len * sizeof(Py_UCS4)) == 0;
}

/* Read a group name up to its terminator, leaving pos past it. 1 on success, -1 on error. */
static int xr_group_name(xr_parser *parser, Py_UCS4 terminator, Py_ssize_t *offset, Py_ssize_t *len) {
    Py_ssize_t start = parser->pos;
    while (parser->pos < parser->len && parser->pattern[parser->pos] != terminator) {
        parser->pos++;
    }
    if (parser->pos >= parser->len) {
        return xr_fail(parser, terminator == '>' ? "missing >, unterminated name" : "missing ), unterminated name",
                       start);
    }
    *offset = start;
    *len = parser->pos++ - start;
    if (*len == 0) {
        return xr_fail(parser, "missing group name", start);
    }
    PyObject *name = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, parser->pattern + start, *len);
    if (name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int valid = th_str_is_identifier(name);
    Py_DECREF(name);
    if (valid < 0) { /* GCOVR_EXCL_BR_LINE: only the PyPy fallback can fail */
        return -1;   /* GCOVR_EXCL_LINE: PyPy method-call failure */
    }
    return valid ? 1 : xr_fail(parser, "bad character in group name", start);
}

/* (?P<name>...) or (?P=name): XR_GROUP_BODY with the name set, a back-reference node, or -1 on error. */
static int32_t xr_parse_named(xr_parser *parser, Py_ssize_t start, Py_ssize_t *offset, Py_ssize_t *len) {
    Py_UCS4 kind = parser->pos < parser->len ? parser->pattern[parser->pos] : 0;
    parser->pos++;
    if (kind != '<' && kind != '=') {
        return xr_fail(parser, "unknown extension ?P", start);
    }
    if (xr_group_name(parser, kind == '<' ? '>' : ')', offset, len) < 0) {
        return -1;
    }
    for (int32_t index = 0; index < parser->name_count; index++) {
        if (xr_name_equal(parser, &parser->names[index], *offset, *len)) {
            return kind == '<' ? xr_fail(parser, "redefinition of group name", start)
                               : xr_backref_node(parser, parser->names[index].group, start);
        }
    }
    return kind == '<' ? XR_GROUP_BODY : xr_fail(parser, "unknown group name", start);
}

static int xr_flag_letter(xr_parser *parser, Py_UCS4 letter, int removing, Py_ssize_t start) {
    switch (letter) {
    case 'i':
        return XR_IGNORECASE;
    case 'm':
        return XR_MULTILINE;
    case 's':
        return XR_DOTALL;
    case 'x':
        return XR_VERBOSE;
    case 'u':
        return removing ? xr_fail(parser, "bad inline flag: cannot turn off flags 'a', 'u' and 'L'", start) : 0;
    case 'a':
        return xr_unsupported("the ASCII-only flag (?a)", "spell out ASCII classes such as [0-9] or [A-Za-z0-9_]");
    default:
        return xr_fail(parser, "unknown flag", start);
    }
}

/* The letters of an inline flag group, starting at its first letter or '-'. Returns ')' for a global group and ':' for
   a scoped one, -1 on error. */
static int xr_parse_flags(xr_parser *parser, Py_ssize_t start, int *add, int *remove) {
    int removing = 0;
    int pending = 0;
    for (;;) {
        if (parser->pos >= parser->len) {
            return xr_fail(parser, "missing -, : or )", start);
        }
        Py_UCS4 letter = parser->pattern[parser->pos++];
        if (letter == '-' && !removing) {
            removing = 1;
            pending = 1;
            continue;
        }
        if ((letter == ')' && !removing) || (letter == ':' && !pending)) {
            if (*add & *remove) {
                return xr_fail(parser, "bad inline flag: flag turned on and off", start);
            }
            return (int)letter;
        }
        int bit = xr_flag_letter(parser, letter, removing, start);
        if (bit < 0) {
            return -1;
        }
        *(removing ? remove : add) |= bit;
        pending = 0;
    }
}

/* What follows "(?": XR_GROUP_BODY for a group (with *capture and *flags set), XR_NO_ITEM when the construct adds
   nothing, a node, or -1 on error. */
static int32_t xr_parse_extension(xr_parser *parser, Py_ssize_t start, int may_set_global, int *capture, int *flags,
                                  Py_ssize_t *offset, Py_ssize_t *len) {
    if (parser->pos >= parser->len) {
        return xr_fail(parser, "unexpected end of pattern", start);
    }
    Py_UCS4 kind = parser->pattern[parser->pos++];
    switch (kind) {
    case 'P':
        return xr_parse_named(parser, start, offset, len);
    case ':':
        *capture = 0;
        return XR_GROUP_BODY;
    case '#':
        while (parser->pos < parser->len && parser->pattern[parser->pos] != ')') {
            parser->pos++;
        }
        if (parser->pos >= parser->len) {
            return xr_fail(parser, "missing ), unterminated comment", start);
        }
        parser->pos++;
        return XR_NO_ITEM;
    case '=':
    case '!':
    case '<':
        return xr_unsupported("look-around (?=...), (?!...), (?<=...), (?<!...), which needs a backtracking matcher",
                              "rewrite the pattern without it");
    case '(':
        return xr_unsupported("conditional groups (?(...)...), which need a backtracking matcher",
                              "use an alternation instead");
    case '>':
        return xr_unsupported("atomic groups (?>...), which need a backtracking matcher", "use a plain group (?:...)");
    default:
        break;
    }
    if (kind != '-' && (kind < 'a' || kind > 'z')) {
        return xr_fail(parser, "unknown extension", start);
    }
    parser->pos--;
    int add = 0;
    int remove = 0;
    int end = xr_parse_flags(parser, start, &add, &remove);
    if (end < 0) {
        return -1;
    }
    if (end == ':') {
        *capture = 0;
        *flags = (*flags | add) & ~remove;
        return XR_GROUP_BODY;
    }
    if (!may_set_global) {
        return xr_fail(parser, "global flags not at the start of the expression", start);
    }
    parser->flags |= add;
    return XR_NO_ITEM;
}

/* A '(' either completes a construct (a back-reference, a comment, global flags) or opens a level whose body the main
   loop parses next, returning XR_GROUP_BODY. */
static int32_t xr_parse_group(xr_parser *parser, int may_set_global) {
    Py_ssize_t start = parser->pos++;
    int capture = 1;
    int flags = parser->flags;
    Py_ssize_t name_offset = 0;
    Py_ssize_t name_len = -1;
    if (parser->pos < parser->len && parser->pattern[parser->pos] == '?') {
        parser->pos++;
        int32_t result = xr_parse_extension(parser, start, may_set_global, &capture, &flags, &name_offset, &name_len);
        if (result != XR_GROUP_BODY) {
            return result;
        }
    }
    int32_t group = -1;
    if (capture) {
        group = ++parser->groups;
        /* GCOVR_EXCL_BR_START: allocation failure */
        if (xr_grow((void **)&parser->closed, &parser->closed_cap, group + 1, 1) < 0) {
            return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        /* GCOVR_EXCL_BR_STOP */
        parser->closed[group] = 0;
        if (name_len >= 0) {
            /* GCOVR_EXCL_BR_START: allocation failure */
            if (xr_grow((void **)&parser->names, &parser->name_cap, parser->name_count + 1, sizeof(xr_name)) < 0) {
                return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            /* GCOVR_EXCL_BR_STOP */
            parser->names[parser->name_count++] = (xr_name){name_offset, name_len, group};
        }
    }
    /* GCOVR_EXCL_BR_START: allocation failure */
    if (xr_grow((void **)&parser->levels, &parser->level_cap, parser->level_count + 1, sizeof(xr_level)) < 0) {
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* GCOVR_EXCL_BR_STOP */
    parser->levels[parser->level_count++] = (xr_level){-1, -1, -1, -1, group, parser->flags, start};
    parser->flags = flags;
    return XR_GROUP_BODY;
}

static int32_t xr_parse_atom(xr_parser *parser, int may_set_global) {
    Py_UCS4 current = parser->pattern[parser->pos];
    switch (current) {
    case '(':
        return xr_parse_group(parser, may_set_global);
    case '[':
        return xr_parse_class(parser);
    case '\\':
        return xr_parse_escape(parser);
    case '.':
        parser->pos++;
        return xr_leaf(parser, XRN_ANY, 0, (parser->flags & XR_DOTALL) != 0);
    case '^':
        parser->pos++;
        return xr_leaf(parser, XRN_ASSERT, parser->flags & XR_MULTILINE ? XRA_MBOL : XRA_START, 0);
    case '$':
        parser->pos++;
        return xr_leaf(parser, XRN_ASSERT, parser->flags & XR_MULTILINE ? XRA_MEOL : XRA_EOL, 0);
    default:
        parser->pos++;
        return xr_char_node(parser, current);
    }
}

static Py_ssize_t xr_parse_count(const xr_parser *parser, Py_ssize_t pos, int32_t *value) {
    *value = 0;
    while (pos < parser->len && is_ascii_digit(parser->pattern[pos])) {
        int32_t digit = (int32_t)(parser->pattern[pos++] - '0');
        *value = *value > (INT32_MAX - digit) / 10 ? INT32_MAX : *value * 10 + digit;
    }
    return pos;
}

/* A quantifier at pos: 1 with its bounds, 0 when the text there is not one (a '{' that does not open a valid {m,n} is a
   literal), -1 on error. */
static int xr_parse_quantifier(xr_parser *parser, int32_t *min, int32_t *max) {
    Py_UCS4 current = parser->pattern[parser->pos];
    if (current == '*' || current == '+' || current == '?') {
        *min = current == '+';
        *max = current == '?' ? 1 : -1;
        parser->pos++;
        return 1;
    }
    if (current != '{' || (parser->pos + 1 < parser->len && parser->pattern[parser->pos + 1] == '}')) {
        return 0;
    }
    Py_ssize_t digits = parser->pos + 1;
    Py_ssize_t cursor = xr_parse_count(parser, digits, min);
    *max = cursor > digits ? *min : -1;
    if (cursor < parser->len && parser->pattern[cursor] == ',') {
        Py_ssize_t upper = cursor + 1;
        cursor = xr_parse_count(parser, upper, max);
        if (cursor == upper) {
            *max = -1;
        }
    }
    if (cursor >= parser->len || parser->pattern[cursor] != '}') {
        return 0;
    }
    if (*max >= 0 && *max < *min) {
        return xr_fail(parser, "min repeat greater than max repeat", parser->pos);
    }
    parser->pos = cursor + 1;
    return 1;
}

/* Wrap the sequence's last item in a repeat, in place, so its list link stays valid. */
static int xr_apply_repeat(xr_parser *parser, int32_t tail, int32_t min, int32_t max, Py_ssize_t start) {
    if (tail < 0 || parser->nodes[tail].kind == XRN_ASSERT) {
        return xr_fail(parser, "nothing to repeat", start);
    }
    if (parser->nodes[tail].kind == XRN_REPEAT) {
        return xr_fail(parser, "multiple repeat", start);
    }
    int greedy = 1;
    if (parser->pos < parser->len && parser->pattern[parser->pos] == '?') {
        greedy = 0;
        parser->pos++;
    } else if (parser->pos < parser->len && parser->pattern[parser->pos] == '+') {
        return xr_unsupported("possessive quantifiers (*+, ++, ?+, {m,n}+), which need a backtracking matcher",
                              "drop the trailing +");
    }
    int32_t inner = xr_node_new(parser, XRN_EMPTY);
    if (inner < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    parser->nodes[inner] = parser->nodes[tail];
    xr_node *repeat = &parser->nodes[tail];
    repeat->kind = XRN_REPEAT;
    repeat->flag = (uint8_t)greedy;
    repeat->child = inner;
    repeat->min = min;
    repeat->max = max;
    repeat->nullable = (uint8_t)(min == 0 || parser->nodes[inner].nullable);
    return 0;
}

static void xr_level_add(xr_parser *parser, xr_level *level, int32_t item) {
    if (level->tail >= 0) {
        parser->nodes[level->tail].next = item;
    } else {
        level->head = item;
    }
    level->tail = item;
}

/* The level's open branch as one node: EMPTY, its single item, or a CONCAT. */
static int32_t xr_end_branch(xr_parser *parser, xr_level *level) {
    int32_t head = level->head;
    int32_t tail = level->tail;
    level->head = -1;
    level->tail = -1;
    if (head < 0) {
        return xr_node_new(parser, XRN_EMPTY);
    }
    if (head == tail) {
        return head;
    }
    int nullable = 1;
    for (int32_t item = head; item >= 0; item = parser->nodes[item].next) {
        nullable &= parser->nodes[item].nullable;
    }
    int32_t concat = xr_node_new(parser, XRN_CONCAT);
    if (concat >= 0) { /* GCOVR_EXCL_BR_LINE: negative only on allocation failure */
        parser->nodes[concat].child = head;
        parser->nodes[concat].nullable = (uint8_t)nullable;
    }
    return concat;
}

/* Close the level's open branch. At a '|' (more set), or after an earlier one, the branch joins the level's
   alternation, which is returned; otherwise the branch is. */
static int32_t xr_close_branch(xr_parser *parser, xr_level *level, int more) {
    int32_t branch = xr_end_branch(parser, level);
    /* GCOVR_EXCL_BR_START: negative only on allocation failure */
    if (branch < 0) {
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* GCOVR_EXCL_BR_STOP */
    if (level->alternation < 0) {
        if (!more) {
            return branch;
        }
        int32_t alternation = xr_node_new(parser, XRN_ALT);
        /* GCOVR_EXCL_BR_START: allocation failure */
        if (alternation < 0) {
            return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        /* GCOVR_EXCL_BR_STOP */
        parser->nodes[alternation].child = branch;
        parser->nodes[alternation].nullable = parser->nodes[branch].nullable;
        level->alternation = alternation;
    } else {
        parser->nodes[level->branch].next = branch;
        parser->nodes[level->alternation].nullable |= parser->nodes[branch].nullable;
    }
    level->branch = branch;
    return level->alternation;
}

/* A ')' ends the innermost level: its body becomes a GROUP item of the level below. */
static int32_t xr_close_group(xr_parser *parser) {
    xr_level *level = &parser->levels[parser->level_count - 1];
    int32_t child = xr_close_branch(parser, level, 0);
    int32_t node = xr_leaf(parser, XRN_GROUP, level->group, 0);
    /* GCOVR_EXCL_BR_START: allocation failure */
    if (child < 0 || node < 0) {
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* GCOVR_EXCL_BR_STOP */
    parser->nodes[node].child = child;
    parser->nodes[node].nullable = parser->nodes[child].nullable;
    if (level->group >= 0) {
        parser->closed[level->group] = 1;
    }
    parser->flags = level->flags;
    parser->level_count--;
    xr_level_add(parser, &parser->levels[parser->level_count - 1], node);
    return node;
}

/* Parse the pattern with an explicit stack of open groups, so nesting depth costs heap rather than C stack: a thread
   with a small stack parses any depth. */
static int32_t xr_parse(xr_parser *parser) {
    /* GCOVR_EXCL_BR_START: allocation failure */
    if (xr_grow((void **)&parser->levels, &parser->level_cap, 1, sizeof(xr_level)) < 0) {
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* GCOVR_EXCL_BR_STOP */
    parser->levels[0] = (xr_level){-1, -1, -1, -1, -1, parser->flags, 0};
    parser->level_count = 1;
    while (parser->pos < parser->len) {
        Py_UCS4 current = parser->pattern[parser->pos];
        xr_level *level = &parser->levels[parser->level_count - 1];
        if (current == ')' && parser->level_count == 1) {
            break;
        }
        if (current == '|' || current == ')') {
            parser->pos++;
            int32_t closed = current == '|' ? xr_close_branch(parser, level, 1) : xr_close_group(parser);
            /* GCOVR_EXCL_BR_START: negative only on allocation failure */
            if (closed < 0) {
                return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            /* GCOVR_EXCL_BR_STOP */
            continue;
        }
        if (parser->flags & XR_VERBOSE) {
            if (current == ' ' || (current >= '\t' && current <= '\r')) {
                parser->pos++;
                continue;
            }
            if (current == '#') {
                while (parser->pos < parser->len && parser->pattern[parser->pos] != '\n') {
                    parser->pos++;
                }
                continue;
            }
        }
        Py_ssize_t start = parser->pos;
        int32_t min;
        int32_t max;
        int quantifier = xr_parse_quantifier(parser, &min, &max);
        if (quantifier != 0) {
            if (quantifier < 0 || xr_apply_repeat(parser, level->tail, min, max, start) < 0) {
                return -1;
            }
            continue;
        }
        int32_t item = xr_parse_atom(parser, parser->level_count == 1 && level->alternation < 0 && level->head < 0);
        if (item == -1) {
            return -1;
        }
        if (item >= 0) {
            xr_level_add(parser, level, item);
        }
    }
    if (parser->level_count > 1) {
        return xr_fail(parser, "missing ), unterminated subpattern", parser->levels[parser->level_count - 1].start);
    }
    return xr_close_branch(parser, &parser->levels[0], 0);
}

enum {
    XRP_START,
    XRP_COPIES,
    XRP_LOOP,
    XRP_LOOP_END,
    XRP_BOUNDED,
    XRP_BOUNDED_NEXT,
    XRP_COPY_A,
    XRP_COPY_B,
    XRP_BRANCH,
    XRP_LAST,
};

/* A node on the compiler's stack, with its progress between the children it compiles. */
typedef struct {
    int32_t node;
    int32_t phase;
    int32_t cursor; /* CONCAT and ALT: the next child; REPEAT: the copies compiled */
    int32_t split;  /* the SPLIT guarding the child in progress */
    int32_t before; /* the instruction count before that child */
    int32_t top;    /* a loop's first instruction */
    int32_t chain;  /* jumps waiting for the node's exit, threaded through next */
    int32_t exits;  /* a bounded repeat's SPLITs waiting for the exit */
    int32_t slot;   /* an iteration's MARK register */
    int32_t first;  /* where an iteration's copy A starts */
    int32_t last;   /* where copy A ends, at its closing JMP */
    int32_t resume; /* the phase to continue with after both copies */
} xr_task;

/* Emission failure (size cap or allocation) is sticky and code[-1] is scratch, so writes through a failed index need no
   check and the compiler tests for failure once, at the end. */
typedef struct {
    const xr_node *nodes;
    xr_inst *code;
    int32_t count, cap;
    int32_t registers;
    int32_t slot_base;
    int failed;
    xr_task *tasks;
    int32_t task_count, task_cap;
} xr_compiler;

static int32_t xr_emit(xr_compiler *compiler, int op, int32_t arg) {
    if (compiler->failed) {
        return -1;
    }
    if (compiler->count >= XR_MAX_CODE) {
        PyErr_Format(PyExc_ValueError,
                     "regular expression compiles to more than %d instructions (10 MiB); lower its repetition "
                     "counts or split it into several patterns",
                     (int)XR_MAX_CODE);
        compiler->failed = 1;
        return -1;
    }
    void *base = compiler->code - 1;
    /* GCOVR_EXCL_BR_START: allocation failure */
    if (xr_grow(&base, &compiler->cap, compiler->count + 2, sizeof(xr_inst)) < 0) {
        compiler->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;            /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    compiler->code = (xr_inst *)base + 1;
    compiler->code[compiler->count] = (xr_inst){(uint8_t)op, arg, compiler->count + 1, -1};
    return compiler->count++;
}

/* Point every instruction on a chain threaded through next (or alt) at target. */
static void xr_patch(xr_compiler *compiler, int32_t chain, int through_alt, int32_t target) {
    while (chain >= 0) {
        int32_t *field = through_alt ? &compiler->code[chain].alt : &compiler->code[chain].next;
        chain = *field;
        *field = target;
    }
}

static void xr_push(xr_compiler *compiler, int32_t node) {
    /* GCOVR_EXCL_BR_START: allocation failure */
    if (xr_grow((void **)&compiler->tasks, &compiler->task_cap, compiler->task_count + 1, sizeof(xr_task)) < 0) {
        compiler->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;               /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    compiler->tasks[compiler->task_count++] =
        (xr_task){node, XRP_START, compiler->nodes[node].child, -1, -1, -1, -1, -1, 0, 0, 0, 0};
}

static void xr_emit_leaf(xr_compiler *compiler, const xr_node *node) {
    switch (node->kind) {
    case XRN_EMPTY:
        break;
    case XRN_CHAR: {
        Py_UCS4 ch = (Py_UCS4)node->value;
        /* CPython's Py_UNICODE_TOUPPER returns the first letter of a full uppercase mapping and PyPy's the simple one,
           which leaves U+00DF and U+FB05 unchanged; FOLD_EXTRA holds their fold partners on both */
        Py_UCS4 folded = xr_fold(ch);
        size_t extra = xr_lower_bound(FOLD_EXTRA, XR_COUNT(FOLD_EXTRA), folded);
        int cased = (extra < XR_COUNT(FOLD_EXTRA) && FOLD_EXTRA[extra].key == folded) || Py_UNICODE_TOLOWER(ch) != ch ||
                    Py_UNICODE_TOUPPER(ch) != ch;
        if (node->flag && cased) {
            xr_emit(compiler, XRI_FOLD, (int32_t)folded);
        } else {
            xr_emit(compiler, XRI_CHAR, node->value);
        }
        break;
    }
    case XRN_ANY:
        xr_emit(compiler, node->flag ? XRI_ANY : XRI_ANY_NL, 0);
        break;
    case XRN_CLASS:
        xr_emit(compiler, XRI_CLASS, node->value);
        break;
    case XRN_ASSERT:
        xr_emit(compiler, XRI_ASSERT, node->value);
        break;
    default:
        xr_emit(compiler, node->flag ? XRI_BACKREF_FOLD : XRI_BACKREF, node->value);
        break;
    }
}

/* re ends a loop after an iteration that consumed nothing and Pike VM states carry no history, so a nullable body
   compiles twice: copy A exits the loop, each consuming step in A continues in copy B, which loops again, and B's CHECK
   against the MARK catches a back-reference that consumed nothing. */
static void xr_start_iteration(xr_compiler *compiler, xr_task *task, int32_t resume) {
    task->slot = compiler->slot_base + compiler->registers++;
    xr_emit(compiler, XRI_MARK, task->slot);
    task->first = compiler->count;
    task->resume = resume;
    task->phase = XRP_COPY_A;
    xr_push(compiler, compiler->nodes[task->node].child);
}

/* 1 after pushing copy B, which the caller must let run; 0 once the task continues at its resume phase. */
static int xr_step_iteration(xr_compiler *compiler, xr_task *task) {
    if (task->phase == XRP_COPY_A) {
        task->last = compiler->count;
        xr_emit(compiler, XRI_JMP, 0);
        task->phase = XRP_COPY_B;
        xr_push(compiler, compiler->nodes[task->node].child);
        return 1;
    }
    int32_t check = xr_emit(compiler, XRI_CHECK, task->slot);
    for (int32_t pc = task->first; pc < task->last; pc++) {
        if (compiler->code[pc].op <= XRI_BACKREF_FOLD) {
            compiler->code[pc].next += task->last + 1 - task->first;
        }
    }
    compiler->code[task->last].next = task->chain;
    compiler->code[check].next = task->last;
    task->chain = check;
    task->phase = task->resume;
    return 0;
}

/* A plus reuses its last mandatory copy as the loop body, which keeps nested pluses from doubling in size. */
static void xr_step_repeat(xr_compiler *compiler, xr_task *task) {
    const xr_node *node = &compiler->nodes[task->node];
    int nullable = compiler->nodes[node->child].nullable;
    int body_first = node->max < 0 && node->min > 0 && !nullable;
    for (;;) {
        switch (task->phase) {
        case XRP_START:
            task->cursor = 0;
            task->phase = XRP_COPIES;
            break;
        case XRP_COPIES:
            /* further copies of an empty-width body add nothing */
            if (task->cursor < node->min - body_first && compiler->count != task->before) {
                task->cursor++;
                task->before = compiler->count;
                xr_push(compiler, node->child);
                return;
            }
            task->cursor = node->min;
            task->phase = node->max < 0 ? XRP_LOOP : XRP_BOUNDED;
            break;
        case XRP_LOOP:
            task->top = compiler->count;
            task->split = body_first ? -1 : xr_emit(compiler, XRI_SPLIT, 0);
            if (nullable) {
                xr_start_iteration(compiler, task, XRP_LOOP_END);
            } else {
                task->phase = XRP_LOOP_END;
                xr_push(compiler, node->child);
            }
            return;
        case XRP_LOOP_END: {
            if (body_first) {
                task->split = xr_emit(compiler, XRI_SPLIT, 0);
            } else {
                int32_t jump = xr_emit(compiler, XRI_JMP, 0);
                compiler->code[jump].next = task->top;
            }
            int32_t again = body_first ? task->top : task->top + 1;
            int32_t exit = compiler->count;
            compiler->code[task->split].next = node->flag ? again : exit;
            compiler->code[task->split].alt = node->flag ? exit : again;
            xr_patch(compiler, task->chain, 0, exit);
            compiler->task_count--;
            return;
        }
        case XRP_BOUNDED:
            if (task->cursor >= node->max) {
                xr_patch(compiler, task->exits, node->flag, compiler->count);
                xr_patch(compiler, task->chain, 0, compiler->count);
                compiler->task_count--;
                return;
            }
            task->split = xr_emit(compiler, XRI_SPLIT, 0);
            task->before = compiler->count;
            if (nullable) {
                xr_start_iteration(compiler, task, XRP_BOUNDED_NEXT);
            } else {
                task->phase = XRP_BOUNDED_NEXT;
                xr_push(compiler, node->child);
            }
            return;
        case XRP_BOUNDED_NEXT: {
            xr_inst *branch = &compiler->code[task->split];
            branch->next = node->flag ? task->split + 1 : task->exits;
            branch->alt = node->flag ? task->exits : task->split + 1;
            task->exits = task->split;
            task->cursor++;
            /* an iteration of an empty body is only its MARK, JMP and CHECK */
            if (compiler->count - task->before <= 3 * nullable) {
                task->cursor = node->max;
            }
            task->phase = XRP_BOUNDED;
            break;
        }
        default:
            if (xr_step_iteration(compiler, task)) {
                return;
            }
            break;
        }
    }
}

static void xr_step_alternation(xr_compiler *compiler, xr_task *task) {
    if (task->phase == XRP_LAST) {
        xr_patch(compiler, task->chain, 0, compiler->count);
        compiler->task_count--;
        return;
    }
    if (task->phase == XRP_BRANCH) {
        int32_t jump = xr_emit(compiler, XRI_JMP, 0);
        compiler->code[jump].next = task->chain;
        task->chain = jump;
        compiler->code[task->split].next = task->split + 1;
        compiler->code[task->split].alt = compiler->count;
    }
    int32_t branch = task->cursor;
    task->cursor = compiler->nodes[branch].next;
    task->phase = task->cursor < 0 ? XRP_LAST : XRP_BRANCH;
    if (task->cursor >= 0) {
        task->split = xr_emit(compiler, XRI_SPLIT, 0);
    }
    xr_push(compiler, branch);
}

/* Lower the tree with an explicit stack of pending nodes, so nesting depth costs heap rather than C stack. */
static void xr_compile_tree(xr_compiler *compiler, int32_t root) {
    xr_push(compiler, root);
    while (compiler->task_count > 0 && !compiler->failed) {
        xr_task *task = &compiler->tasks[compiler->task_count - 1];
        const xr_node *node = &compiler->nodes[task->node];
        switch (node->kind) {
        case XRN_GROUP:
            if (task->phase == XRP_START) {
                if (node->value >= 0) {
                    xr_emit(compiler, XRI_SAVE, 2 * node->value);
                }
                task->phase = XRP_LAST;
                xr_push(compiler, node->child);
            } else {
                if (node->value >= 0) {
                    xr_emit(compiler, XRI_SAVE, 2 * node->value + 1);
                }
                compiler->task_count--;
            }
            break;
        case XRN_CONCAT:
            if (task->cursor < 0) {
                compiler->task_count--;
            } else {
                int32_t item = task->cursor;
                task->cursor = compiler->nodes[item].next;
                xr_push(compiler, item);
            }
            break;
        case XRN_ALT:
            xr_step_alternation(compiler, task);
            break;
        case XRN_REPEAT:
            xr_step_repeat(compiler, task);
            break;
        default:
            xr_emit_leaf(compiler, node);
            compiler->task_count--;
            break;
        }
    }
}

static void xr_program_free(xr_program *program) {
    PyMem_Free(program->pattern);
    PyMem_Free(program->code - 1);
    PyMem_Free(program->classes);
    PyMem_Free(program->ranges);
    PyMem_Free(program->names);
    PyMem_Free(program->states);
    PyMem_Free(program->cells);
    PyMem_Free(program->frames);
    PyMem_Free(program->scratch);
    PyMem_Free(program->trail);
    PyMem_Free(program);
}

static xr_program *xr_assemble(xr_parser *parser, int32_t root, int flags) {
    xr_compiler compiler = {0};
    compiler.nodes = parser->nodes;
    compiler.slot_base = 2 * (parser->groups + 1);
    compiler.code = PyMem_Malloc(16 * sizeof(xr_inst));
    /* GCOVR_EXCL_BR_START: allocation failure */
    if (compiler.code == NULL) {
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    compiler.code++;
    compiler.cap = 16;
    xr_emit(&compiler, XRI_SAVE, 0);
    xr_compile_tree(&compiler, root);
    xr_emit(&compiler, XRI_SAVE, 1);
    xr_emit(&compiler, XRI_MATCH, 0);
    PyMem_Free(compiler.tasks);
    if (compiler.failed) {
        PyMem_Free(compiler.code - 1);
        return NULL;
    }
    xr_program *program = PyMem_Calloc(1, sizeof(xr_program));
    Py_UCS4 *pattern = PyMem_Malloc((size_t)(parser->len + 1) * sizeof(Py_UCS4));
    if (program == NULL || pattern == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyMem_Free(program);                  /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(pattern);                  /* GCOVR_EXCL_LINE */
        PyMem_Free(compiler.code - 1);        /* GCOVR_EXCL_LINE */
        PyErr_NoMemory();                     /* GCOVR_EXCL_LINE */
        return NULL;                          /* GCOVR_EXCL_LINE */
    }
    memcpy(pattern, parser->pattern, (size_t)parser->len * sizeof(Py_UCS4));
    program->pattern = pattern;
    program->pattern_len = parser->len;
    program->flags = flags;
    program->code = compiler.code;
    program->code_count = compiler.count;
    program->classes = parser->classes;
    program->ranges = parser->ranges;
    program->names = parser->names;
    program->name_count = parser->name_count;
    parser->classes = NULL;
    parser->ranges = NULL;
    parser->names = NULL;
    program->groups = parser->groups;
    program->slots = compiler.slot_base;
    program->registers = compiler.registers;
    program->backrefs = (uint8_t)parser->backrefs;
    int32_t pc = 0;
    while (program->code[pc].op == XRI_SAVE) {
        pc++;
    }
    program->anchored = (uint8_t)(program->code[pc].op == XRI_ASSERT && program->code[pc].arg == XRA_START);
    program->prefilter = program->code[pc].op <= XRI_CLASS ? pc : -1;
    while (program->code[pc + program->prefix].op == XRI_CHAR) {
        program->prefix++;
    }
    return program;
}

static xr_program *xr_compile(const Py_UCS4 *pattern, Py_ssize_t len, int flags, xr_error *error) {
    xr_parser parser = {0};
    parser.pattern = pattern;
    parser.len = len;
    parser.flags = flags;
    parser.error = error;
    xr_program *program = NULL;
    int32_t root = xr_parse(&parser);
    if (root >= 0 && parser.pos < parser.len) {
        root = xr_fail(&parser, "unbalanced parenthesis", parser.pos);
    }
    if (root >= 0) {
        program = xr_assemble(&parser, root, flags);
    }
    PyMem_Free(parser.levels);
    PyMem_Free(parser.nodes);
    PyMem_Free(parser.classes);
    PyMem_Free(parser.ranges);
    PyMem_Free(parser.closed);
    PyMem_Free(parser.names);
    return program;
}

static int xr_prepare(xr_program *program, int captures) {
    size_t count = (size_t)program->code_count;
    if (program->frames == NULL) {
        program->frames = PyMem_Malloc((count + 1) * sizeof(xr_frame));
        program->scratch = PyMem_Malloc((size_t)(program->slots + program->registers) * sizeof(Py_ssize_t));
        program->states = PyMem_Calloc(4 * count, sizeof(int32_t));
        /* GCOVR_EXCL_BR_START: allocation failure */
        if (program->frames == NULL || program->scratch == NULL || program->states == NULL) {
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_BR_STOP */
        for (size_t index = 0; index < 2; index++) {
            program->lists[index].dense = program->states + 2 * index * count;
            program->lists[index].sparse = program->states + (2 * index + 1) * count;
        }
    }
    if (captures && program->cells == NULL) {
        size_t width = (size_t)program->slots;
        if ((2 * count + 1) * width * sizeof(Py_ssize_t) > XR_MAX_BYTES) {
            PyErr_Format(PyExc_ValueError,
                         "replacing needs more than 10 MiB of capture slots for this regular expression (%zu "
                         "instructions times %d groups); make groups the replacement does not use non-capturing "
                         "with (?:...)",
                         count, (int)program->groups);
            return -1;
        }
        program->cells = PyMem_Malloc((2 * count + 1) * width * sizeof(Py_ssize_t));
        if (program->cells == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            PyErr_NoMemory();         /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                /* GCOVR_EXCL_LINE */
        }
        program->lists[0].slots = program->cells;
        program->lists[1].slots = program->cells + count * width;
        program->best = program->cells + 2 * count * width;
    }
    return 0;
}

/* The first position at or after pos where the literal prefix occurs or the first instruction accepts, len if none. */
static Py_ssize_t xr_skip(const xr_program *program, const Py_UCS4 *text, Py_ssize_t len, Py_ssize_t pos) {
    const xr_inst *first = &program->code[program->prefilter];
    if (program->prefix == 0) {
        while (pos < len && !xr_consume(program, first, text[pos])) {
            pos++;
        }
        return pos;
    }
    Py_UCS4 lead = (Py_UCS4)first->arg;
    for (Py_ssize_t last = len - program->prefix; pos <= last; pos++) {
        if (text[pos] == lead) {
            int32_t matched = 1;
            while (matched < program->prefix && text[pos + matched] == (Py_UCS4)first[matched].arg) {
                matched++;
            }
            if (matched == program->prefix) {
                return pos;
            }
        }
    }
    return len;
}

/* Follow the empty-width instructions from pc at pos, adding each state to list in priority order and recording the
   capture slots of the consuming ones. */
static void xr_add(xr_program *program, xr_list *list, int32_t pc, const Py_UCS4 *text, Py_ssize_t len, Py_ssize_t pos,
                   int32_t width) {
    Py_ssize_t *caps = program->scratch;
    xr_frame *stack = program->frames;
    int32_t top = 0;
    stack[top++] = (xr_frame){pc, -1, 0};
    while (top > 0) {
        xr_frame frame = stack[--top];
        if (frame.slot >= 0) {
            caps[frame.slot] = frame.value;
            continue;
        }
        pc = frame.pc;
        for (;;) {
            int32_t seen = list->sparse[pc];
            if (seen < list->len && list->dense[seen] == pc) {
                break;
            }
            list->sparse[pc] = list->len;
            list->dense[list->len++] = pc;
            const xr_inst *inst = &program->code[pc];
            if (inst->op == XRI_JMP) {
                pc = inst->next;
            } else if (inst->op == XRI_SPLIT) {
                stack[top++] = (xr_frame){inst->alt, -1, 0};
                pc = inst->next;
            } else if (inst->op == XRI_SAVE) {
                if (width > 0) {
                    stack[top++] = (xr_frame){0, inst->arg, caps[inst->arg]};
                    caps[inst->arg] = pos;
                }
                pc++;
            } else if (inst->op == XRI_MARK || inst->op == XRI_CHECK) {
                /* a Pike VM program has no back-references, so copy B has always consumed */
                pc++;
            } else if (inst->op == XRI_ASSERT) {
                if (!xr_assert(inst->arg, text, len, pos)) {
                    break;
                }
                pc++;
            } else {
                if (width > 0) {
                    memcpy(list->slots + (size_t)pc * (size_t)width, caps, (size_t)width * sizeof(Py_ssize_t));
                }
                break;
            }
        }
    }
}

static int xr_pike(xr_program *program, const Py_UCS4 *text, Py_ssize_t len, Py_ssize_t start, int must_advance,
                   Py_ssize_t *spans) {
    int32_t width = spans == NULL ? 0 : program->slots;
    if (xr_prepare(program, width > 0) < 0) {
        return -1;
    }
    xr_list *current = &program->lists[0];
    xr_list *next = &program->lists[1];
    current->len = 0;
    int matched = 0;
    for (Py_ssize_t pos = start; pos <= len; pos++) {
        if (!matched && (pos == start || !program->anchored)) {
            if (current->len == 0 && program->prefilter >= 0) {
                pos = xr_skip(program, text, len, pos);
                if (pos == len) {
                    break;
                }
            }
            for (int32_t slot = 0; slot < width; slot++) {
                program->scratch[slot] = -1;
            }
            xr_add(program, current, 0, text, len, pos, width);
        }
        if (current->len == 0) {
            break;
        }
        next->len = 0;
        for (int32_t index = 0; index < current->len; index++) {
            int32_t pc = current->dense[index];
            const xr_inst *inst = &program->code[pc];
            if (inst->op == XRI_MATCH) {
                if (must_advance && pos == start) {
                    continue;
                }
                if (width == 0) {
                    return 1;
                }
                matched = 1;
                memcpy(program->best, current->slots + (size_t)pc * (size_t)width, (size_t)width * sizeof(Py_ssize_t));
                break;
            }
            if (pos < len && xr_consume(program, inst, text[pos])) {
                if (width > 0) {
                    memcpy(program->scratch, current->slots + (size_t)pc * (size_t)width,
                           (size_t)width * sizeof(Py_ssize_t));
                }
                xr_add(program, next, inst->next, text, len, pos + 1, width);
            }
        }
        xr_list *swap = current;
        current = next;
        next = swap;
    }
    if (matched) {
        memcpy(spans, program->best, (size_t)(2 * (program->groups + 1)) * sizeof(Py_ssize_t));
    }
    return matched;
}

static int xr_exhausted(const xr_cache *cache) {
    PyErr_Format(PyExc_ValueError,
                 "regular expression back-references need more than %zd backtracking steps (10000000 plus 100 per "
                 "character searched in this XPath evaluation); simplify the pattern or drop the back-reference",
                 cache->allowance);
    return -1;
}

/* Room for the one frame a backtracking step can push, within the trail's cap. */
static int xr_reserve(xr_program *program, Py_ssize_t depth) {
    if (depth < program->trail_cap) {
        return 0;
    }
    if (program->trail_cap >= XR_MAX_TRAIL) {
        PyErr_Format(PyExc_ValueError,
                     "regular expression back-references need more than %zd saved backtracking positions (10 MiB); "
                     "search a shorter string or drop the back-reference",
                     XR_MAX_TRAIL);
        return -1;
    }
    Py_ssize_t cap = program->trail_cap == 0 ? 64 : program->trail_cap * 2;
    if (cap > XR_MAX_TRAIL) {
        cap = XR_MAX_TRAIL;
    }
    xr_frame *grown = PyMem_Realloc(program->trail, (size_t)cap * sizeof(xr_frame));
    if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    program->trail = grown;
    program->trail_cap = cap;
    return 0;
}

/* re compares a case-insensitive back-reference by lowercase, without the fold sets. */
static int xr_backref(const xr_inst *inst, const Py_ssize_t *caps, const Py_UCS4 *text, Py_ssize_t len,
                      Py_ssize_t *pos) {
    Py_ssize_t slot = 2 * (Py_ssize_t)inst->arg;
    Py_ssize_t from = caps[slot];
    if (from < 0) {
        return 0;
    }
    Py_ssize_t size = caps[slot + 1] - from;
    if (size > len - *pos) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < size; index++) {
        Py_UCS4 captured = text[from + index];
        Py_UCS4 ch = text[*pos + index];
        if (inst->op == XRI_BACKREF_FOLD ? xr_lower(captured) != xr_lower(ch) : captured != ch) {
            return 0;
        }
    }
    *pos += size;
    return 1;
}

/* One leftmost-first attempt at origin: 1 on a match, 0 when every path fails, -1 when the budget or trail runs out. */
static int xr_backtrack_at(xr_program *program, xr_cache *cache, const Py_UCS4 *text, Py_ssize_t len, Py_ssize_t origin,
                           Py_ssize_t start, int must_advance, Py_ssize_t *spans) {
    Py_ssize_t *caps = program->scratch;
    for (int32_t slot = 0; slot < program->slots + program->registers; slot++) {
        caps[slot] = -1;
    }
    Py_ssize_t depth = 0;
    int32_t pc = 0;
    Py_ssize_t pos = origin;
    for (;;) {
        if (--cache->budget < 0) {
            return xr_exhausted(cache);
        }
        if (xr_reserve(program, depth) < 0) {
            return -1;
        }
        const xr_inst *inst = &program->code[pc];
        int ok;
        switch (inst->op) {
        case XRI_JMP:
            pc = inst->next;
            continue;
        case XRI_SPLIT:
            program->trail[depth++] = (xr_frame){inst->alt, -1, pos};
            pc = inst->next;
            continue;
        case XRI_SAVE:
        case XRI_MARK:
            program->trail[depth++] = (xr_frame){0, inst->arg, caps[inst->arg]};
            caps[inst->arg] = pos;
            pc = inst->next;
            continue;
        case XRI_CHECK:
            pc = caps[inst->arg] == pos ? inst->next : pc + 1;
            continue;
        case XRI_ASSERT:
            ok = xr_assert(inst->arg, text, len, pos);
            break;
        case XRI_BACKREF:
        case XRI_BACKREF_FOLD:
            ok = xr_backref(inst, caps, text, len, &pos);
            break;
        case XRI_MATCH:
            if (!(must_advance && pos == start)) {
                if (spans != NULL) {
                    memcpy(spans, caps, (size_t)(2 * (program->groups + 1)) * sizeof(Py_ssize_t));
                }
                return 1;
            }
            ok = 0;
            break;
        default:
            ok = pos < len && xr_consume(program, inst, text[pos]);
            pos += ok;
            break;
        }
        if (ok) {
            pc = inst->next;
            continue;
        }
        for (;;) {
            if (depth == 0) {
                return 0;
            }
            const xr_frame *frame = &program->trail[--depth];
            if (frame->slot < 0) {
                pc = frame->pc;
                pos = frame->value;
                break;
            }
            caps[frame->slot] = frame->value;
        }
    }
}

static int xr_search(xr_program *program, xr_cache *cache, const Py_UCS4 *text, Py_ssize_t len, Py_ssize_t start,
                     int must_advance, Py_ssize_t *spans) {
    if (!program->backrefs) {
        return xr_pike(program, text, len, start, must_advance, spans);
    }
    if (xr_prepare(program, 0) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t origin = start; origin <= len && (origin == start || !program->anchored); origin++) {
        if (program->prefilter >= 0) {
            origin = xr_skip(program, text, len, origin);
            if (origin == len) {
                break;
            }
        }
        int found = xr_backtrack_at(program, cache, text, len, origin, start, must_advance, spans);
        if (found != 0) {
            return found;
        }
    }
    return 0;
}

static size_t xr_hash(const Py_UCS4 *pattern, Py_ssize_t len) {
    size_t hash = 2166136261u;
    for (Py_ssize_t index = 0; index < len; index++) {
        hash = (hash ^ pattern[index]) * 16777619u;
    }
    return hash;
}

/* Out of line, and only called on a cache that exists: inlined, it made LTO stop inlining xp_eval_at into the XSLT
   callers, which cost 6.4% on the transform-key benchmark. */
TH_NOINLINE void xr_cache_free(xr_cache *cache) {
    for (int index = 0; index < XR_CACHE_SLOTS; index++) {
        if (cache->programs[index] != NULL) {
            xr_program_free(cache->programs[index]);
        }
    }
    PyMem_Free(cache);
}

/* The compiled program for pattern, from the evaluation's cache when it holds one, and the budget topped up for a
   search over text_len characters. */
static xr_program *xr_cache_get(xr_cache **cache_ref, const Py_UCS4 *pattern, Py_ssize_t len, int flags,
                                Py_ssize_t text_len, xr_error *error) {
    error->pattern_error = 0;
    xr_cache *cache = *cache_ref;
    if (cache == NULL) {
        cache = PyMem_Calloc(1, sizeof(xr_cache));
        if (cache == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;      /* GCOVR_EXCL_LINE */
        }
        *cache_ref = cache;
        cache->allowance = XR_BUDGET_BASE;
        cache->budget = XR_BUDGET_BASE;
    }
    cache->allowance += text_len * XR_BUDGET_PER_CHAR;
    cache->budget += text_len * XR_BUDGET_PER_CHAR;
    xr_program **slot = &cache->programs[xr_hash(pattern, len) & (XR_CACHE_SLOTS - 1)];
    xr_program *program = *slot;
    if (program != NULL && program->flags == flags && program->pattern_len == len &&
        memcmp(program->pattern, pattern, (size_t)len * sizeof(Py_UCS4)) == 0) {
        return program;
    }
    program = xr_compile(pattern, len, flags, error);
    if (program != NULL) {
        if (*slot != NULL) {
            xr_program_free(*slot);
        }
        *slot = program;
    }
    return program;
}

int xr_test(xr_cache **cache, const Py_UCS4 *pattern, Py_ssize_t pattern_len, int flags, const Py_UCS4 *text,
            Py_ssize_t text_len, xr_error *error) {
    xr_program *program = xr_cache_get(cache, pattern, pattern_len, flags, text_len, error);
    return program == NULL ? -1 : xr_search(program, *cache, text, text_len, 0, 0, NULL);
}

typedef struct {
    Py_UCS4 *text;
    Py_ssize_t len;
    Py_ssize_t cap;
} xr_buffer;

static int xr_append(xr_buffer *buffer, const Py_UCS4 *text, Py_ssize_t len) {
    if (buffer->len + len > buffer->cap) {
        size_t cap, bytes;
        /* GCOVR_EXCL_BR_START: size overflow */
        if (!th_grow_cap((size_t)(buffer->len + len), (size_t)buffer->cap, 64, sizeof(Py_UCS4), &cap, &bytes)) {
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: size-overflow path */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_BR_STOP */
        Py_UCS4 *grown = PyMem_Realloc(buffer->text, bytes);
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        buffer->text = grown;
        buffer->cap = (Py_ssize_t)cap;
    }
    if (len > 0) {
        memcpy(buffer->text + buffer->len, text, (size_t)len * sizeof(Py_UCS4));
    }
    buffer->len += len;
    return 0;
}

/* A parsed replacement: literal runs of `literal` (group -1) and group references. */
typedef struct {
    Py_ssize_t group;
    Py_ssize_t offset;
    Py_ssize_t len;
} xr_part;

typedef struct {
    xr_part *parts;
    int32_t count, cap;
    Py_UCS4 *literal;
    Py_ssize_t literal_len;
    Py_ssize_t run;
    xr_error *error;
} xr_template;

static int xr_template_part(xr_template *template, Py_ssize_t group) {
    /* GCOVR_EXCL_BR_START: allocation failure */
    if (xr_grow((void **)&template->parts, &template->cap, template->count + 1, sizeof(xr_part)) < 0) {
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* GCOVR_EXCL_BR_STOP */
    template->parts[template->count++] = (xr_part){group, template->run, template->literal_len - template->run};
    template->run = template->literal_len;
    return 0;
}

static void xr_template_char(xr_template *template, Py_UCS4 ch) {
    template->literal[template->literal_len++] = ch;
}

static int xr_template_group(xr_template *template, Py_ssize_t group) {
    /* GCOVR_EXCL_BR_START: allocation failure */
    return xr_template_part(template, -1) < 0 ? -1 : xr_template_part(template, group);
    /* GCOVR_EXCL_BR_STOP */
}

static int xr_template_fail(xr_template *template, const char *message, Py_ssize_t position) {
    template->error->pattern_error = 1;
    PyOS_snprintf(template->error->message, sizeof(template->error->message), "%s at position %lld", message,
                  (long long)position);
    return -1;
}

static int xr_template_reference(xr_template *template, const xr_program *program, Py_ssize_t group,
                                 Py_ssize_t position) {
    if (group > program->groups) {
        char message[64];
        PyOS_snprintf(message, sizeof(message), "invalid group reference %lld", (long long)group);
        return xr_template_fail(template, message, position);
    }
    return xr_template_group(template, group);
}

/* \g<name> or \g<number> in a re.sub template, starting after the 'g'. */
static int xr_template_named(xr_template *template, const xr_program *program, const Py_UCS4 *text, Py_ssize_t len,
                             Py_ssize_t *index) {
    Py_ssize_t start = *index - 2;
    if (*index >= len || text[*index] != '<') {
        return xr_template_fail(template, "missing <", start);
    }
    Py_ssize_t name = ++*index;
    while (*index < len && text[*index] != '>') {
        ++*index;
    }
    if (*index >= len) {
        return xr_template_fail(template, "missing >, unterminated name", start);
    }
    Py_ssize_t name_len = (*index)++ - name;
    if (name_len == 0) {
        return xr_template_fail(template, "missing group name", start);
    }
    Py_ssize_t group = 0;
    Py_ssize_t digit = 0;
    while (digit < name_len && is_ascii_digit(text[name + digit])) {
        group = group > 100000000 ? group : group * 10 + (Py_ssize_t)(text[name + digit] - '0');
        digit++;
    }
    if (digit == name_len) {
        return xr_template_reference(template, program, group, start);
    }
    for (int32_t entry = 0; entry < program->name_count; entry++) {
        const xr_name *known = &program->names[entry];
        if (known->len == name_len &&
            memcmp(program->pattern + known->offset, text + name, (size_t)name_len * sizeof(Py_UCS4)) == 0) {
            return xr_template_group(template, known->group);
        }
    }
    PyObject *text_name = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, text + name, name_len);
    if (text_name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;           /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int valid = th_str_is_identifier(text_name);
    if (valid < 0) {          /* GCOVR_EXCL_BR_LINE: only the PyPy fallback can fail */
        Py_DECREF(text_name); /* GCOVR_EXCL_LINE: PyPy method-call failure */
        return -1;            /* GCOVR_EXCL_LINE: PyPy method-call failure */
    }
    if (!valid) {
        Py_DECREF(text_name);
        return xr_template_fail(template, "bad character in group name", start);
    }
    PyErr_Format(PyExc_IndexError, "unknown group name '%U'", text_name);
    Py_DECREF(text_name);
    return -1;
}

/* A backslash escape in a re.sub template, starting after the backslash. */
static int xr_template_escape(xr_template *template, const xr_program *program, const Py_UCS4 *text, Py_ssize_t len,
                              Py_ssize_t *index) {
    Py_ssize_t start = *index - 1;
    if (*index >= len) {
        return xr_template_fail(template, "bad escape (end of pattern)", start);
    }
    Py_UCS4 letter = text[(*index)++];
    if (letter == 'g') {
        return xr_template_named(template, program, text, len, index);
    }
    if (is_ascii_digit(letter)) {
        Py_ssize_t digits = 1;
        while (digits < 3 && *index < len && is_ascii_digit(text[*index])) {
            digits++;
            (*index)++;
        }
        const Py_UCS4 *number = text + start + 1;
        int octal = letter == '0' || (digits == 3 && number[0] <= '7' && number[1] <= '7' && number[2] <= '7');
        if (!octal) {
            /* two digits name a group; a third belongs to the literal text after it */
            *index = start + 1 + (digits > 2 ? 2 : digits);
            Py_ssize_t group = digits > 1 ? (number[0] - '0') * 10 + (number[1] - '0') : number[0] - '0';
            return xr_template_reference(template, program, group, start);
        }
        Py_ssize_t value = 0;
        Py_ssize_t used = 0;
        while (used < digits && number[used] <= '7') {
            value = value * 8 + (number[used++] - '0');
        }
        *index = start + 1 + used;
        if (value > 0377) {
            return xr_template_fail(template, "octal escape value outside of range 0-0o377", start);
        }
        xr_template_char(template, (Py_UCS4)(value & 0xFF));
        return 0;
    }
    static const char SIMPLE[] = "a\ab\bf\fn\nr\rt\tv\v\\\\";
    for (size_t entry = 0; entry + 1 < sizeof(SIMPLE); entry += 2) {
        if ((Py_UCS4)(unsigned char)SIMPLE[entry] == letter) {
            xr_template_char(template, (Py_UCS4)(unsigned char)SIMPLE[entry + 1]);
            return 0;
        }
    }
    if (is_ascii_alpha(letter)) {
        return xr_template_fail(template, "bad escape", start);
    }
    xr_template_char(template, '\\');
    xr_template_char(template, letter);
    return 0;
}

/* $N in an fn:replace replacement, starting at the first digit: the longest prefix of the digits naming a group is the
   reference, the rest is literal text, and a single digit past the group count stands for nothing. */
static int xr_template_dollar(xr_template *template, const xr_program *program, const Py_UCS4 *text, Py_ssize_t len,
                              Py_ssize_t *index) {
    Py_ssize_t group = -1;
    Py_ssize_t value = 0;
    Py_ssize_t cursor = *index;
    Py_ssize_t used = *index + 1;
    while (cursor < len && is_ascii_digit(text[cursor])) {
        value = value * 10 + (Py_ssize_t)(text[cursor++] - '0');
        if (value > program->groups) {
            break;
        }
        group = value;
        used = cursor;
    }
    *index = used;
    return group < 0 ? 0 : xr_template_group(template, group);
}

static int xr_template_parse(xr_template *template, const xr_program *program, const Py_UCS4 *text, Py_ssize_t len,
                             int xpath_syntax) {
    template->literal = PyMem_Malloc((size_t)(len + 1) * sizeof(Py_UCS4));
    if (template->literal == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyErr_NoMemory();            /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;                   /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t index = 0;
    while (index < len) {
        Py_UCS4 ch = text[index++];
        int rc = 0;
        if (xpath_syntax) {
            if (ch == '\\' && index < len && (text[index] == '\\' || text[index] == '$')) {
                xr_template_char(template, text[index++]);
            } else if (ch == '$' && index < len && is_ascii_digit(text[index])) {
                rc = xr_template_dollar(template, program, text, len, &index);
            } else {
                xr_template_char(template, ch);
            }
        } else if (ch == '\\') {
            rc = xr_template_escape(template, program, text, len, &index);
        } else {
            xr_template_char(template, ch);
        }
        if (rc < 0) {
            return -1;
        }
    }
    return xr_template_part(template, -1);
}

static int xr_expand(xr_buffer *buffer, const xr_template *template, const Py_UCS4 *text, const Py_ssize_t *spans) {
    for (int32_t index = 0; index < template->count; index++) {
        const xr_part *part = &template->parts[index];
        int rc;
        if (part->group < 0) {
            rc = xr_append(buffer, template->literal + part->offset, part->len);
        } else {
            Py_ssize_t from = spans[2 * part->group];
            /* a group that did not take part in the match stands for the empty string */
            rc = from < 0 ? 0 : xr_append(buffer, text + from, spans[2 * part->group + 1] - from);
        }
        if (rc < 0) {  /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return 0;
}

int xr_replace(xr_cache **cache, const Py_UCS4 *pattern, Py_ssize_t pattern_len, int flags, const Py_UCS4 *text,
               Py_ssize_t text_len, const Py_UCS4 *replacement, Py_ssize_t replacement_len, int xpath_syntax,
               int first_only, Py_UCS4 **out, Py_ssize_t *out_len, xr_error *error) {
    xr_program *program = xr_cache_get(cache, pattern, pattern_len, flags, text_len, error);
    if (program == NULL) {
        return -1;
    }
    Py_ssize_t *spans = PyMem_Malloc((size_t)(2 * (program->groups + 1)) * sizeof(Py_ssize_t));
    if (spans == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    xr_template template = {0};
    template.error = error;
    xr_buffer buffer = {0};
    int rc = xr_template_parse(&template, program, replacement, replacement_len, xpath_syntax);
    Py_ssize_t last = 0;
    Py_ssize_t pos = 0;
    int must_advance = 0;
    while (rc == 0) {
        int found = xr_search(program, *cache, text, text_len, pos, must_advance, spans);
        if (found <= 0) {
            rc = found;
            break;
        }
        /* GCOVR_EXCL_BR_START: allocation failure */
        if (xr_append(&buffer, text + last, spans[0] - last) < 0 || xr_expand(&buffer, &template, text, spans) < 0) {
            rc = -1; /* GCOVR_EXCL_LINE: allocation-failure path */
            break;   /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_BR_STOP */
        last = spans[1];
        pos = spans[1];
        must_advance = spans[0] == spans[1];
        if (first_only) {
            break;
        }
    }
    if (rc == 0) {
        rc = xr_append(&buffer, text + last, text_len - last);
    }
    if (rc == 0 && buffer.text == NULL) {
        buffer.text = PyMem_Malloc(sizeof(Py_UCS4));
        rc = buffer.text == NULL ? -1 : 0; /* GCOVR_EXCL_BR_LINE: allocation failure */
    }
    PyMem_Free(spans);
    PyMem_Free(template.parts);
    PyMem_Free(template.literal);
    if (rc < 0) {
        PyMem_Free(buffer.text);
        return -1;
    }
    *out = buffer.text;
    *out_len = buffer.len;
    return 0;
}
