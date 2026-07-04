/* A CSS selector engine over the node tree, #included into dom/node.c. A
   selector is compiled against the tree it will run on, so tag and attribute
   names resolve to interned atoms once and every match is an integer compare.
   Matching is right-to-left with backtracking on the descendant and general
   sibling combinators. This covers the common subset: type, universal, class,
   id, attribute operators, the four combinators, the :is()/:where()/:has()/:not()
   functional pseudo-classes, all grouped by commas. */

#ifndef TURBOHTML_SELECTOR_H
#define TURBOHTML_SELECTOR_H

#include "core/ascii.h"
#include "dom/tree.h"

enum sel_attr_op { OP_EXISTS, OP_EQ, OP_INCLUDE, OP_DASH, OP_PREFIX, OP_SUFFIX, OP_SUBSTR };

/* The functional pseudo-classes hold a nested selector list built from sel_complex,
   so the type is forward-declared here. */
typedef struct sel_complex sel_complex;

/* ':' pseudo-classes. The structural ones have fixed meaning (the nth-* variants
   carry An+B in nth_a/nth_b); the functional ones (:is/:where/:has/:not) carry a
   nested selector list in sub. */
enum sel_pseudo {
    PSEUDO_NONE,
    PSEUDO_ROOT,
    PSEUDO_EMPTY,
    PSEUDO_FIRST_CHILD,
    PSEUDO_LAST_CHILD,
    PSEUDO_ONLY_CHILD,
    PSEUDO_FIRST_OF_TYPE,
    PSEUDO_LAST_OF_TYPE,
    PSEUDO_ONLY_OF_TYPE,
    PSEUDO_NTH_CHILD,
    PSEUDO_NTH_LAST_CHILD,
    PSEUDO_NTH_OF_TYPE,
    PSEUDO_NTH_LAST_OF_TYPE,
    PSEUDO_IS,
    PSEUDO_WHERE,
    PSEUDO_HAS,
    PSEUDO_NOT,
    /* §6.6 the scoping root, and the §12 input pseudo-classes determinable from the
       static tree */
    PSEUDO_SCOPE,
    PSEUDO_CHECKED,
    PSEUDO_DISABLED,
    PSEUDO_ENABLED,
    PSEUDO_REQUIRED,
    PSEUDO_OPTIONAL,
    PSEUDO_READ_ONLY,
    PSEUDO_READ_WRITE,
    PSEUDO_DEFAULT,
    PSEUDO_LANG, /* §11.1, the comma list of ranges is stored as the value slice */
    PSEUDO_DIR,  /* §11.2, the direction (1 ltr, 2 rtl, 0 other) is stored in nth_a */
    /* :link/:any-link: an unvisited or any hyperlink. A parsed tree has no visit
       history, so both reduce to :is(a, area)[href] (HTML "the :link/:any-link") */
    PSEUDO_ANY_LINK,
    /* live UA/interaction or navigation state a static tree cannot express: these
       parse as valid selectors but match nothing (so :is()/:not() still compose) */
    PSEUDO_NEVER,
};

typedef struct {
    char kind;          /* '*', 'e' type, '.' class, '#' id, '[' attribute, ':' pseudo-class */
    uint16_t tag_atom;  /* 'e': the tag atom, TH_TAG_UNKNOWN for a name outside the table */
    uint32_t attr_atom; /* '[': the attribute atom, UINT32_MAX when no element has the name */
    enum sel_attr_op op;
    int ci;               /* '[': matched case-insensitively (explicit i flag) */
    int ci_default;       /* '[': name is in the HTML case-insensitive set, no explicit s/i flag */
    int pseudo;           /* ':': the pseudo-class id (enum sel_pseudo) */
    int nth_a;            /* ':': the An+B "A" coefficient for an nth-* pseudo-class; :dir() direction code */
    int nth_b;            /* ':': the An+B "B" coefficient for an nth-* pseudo-class */
    const Py_UCS4 *name;  /* class / id / unknown tag name (into the owned source copy) */
    Py_ssize_t name_len;  /* also the attribute name for the rare unknown case */
    const Py_UCS4 *value; /* '[': the attribute value; ':' the :lang() comma list of ranges */
    Py_ssize_t value_len;
    sel_complex *sub; /* ':': the nested selector list for :is()/:where()/:has()/:not() */
    int sub_count;    /* ':': number of comma-separated alternatives in sub */
} sel_simple;

typedef struct {
    sel_simple *simples;
    int count;
    char combinator; /* ' ', '>', '+', '~': the combinator joining the compound on the left */
} sel_compound;

struct sel_complex {
    sel_compound *compounds; /* left to right; matched from the rightmost (the subject) */
    int count;
};

typedef struct {
    Py_UCS4 *source; /* an owned copy of the selector text the slices point into */
    sel_complex *alts;
    int count;
    int failed;    /* an allocation or a syntax error happened during compile */
    int quirks;    /* the tree was parsed in quirks mode: class/ID match case-insensitively */
    th_tree *tree; /* the tree the selector runs on; :empty and :dir(auto) read text spans through it */
} sel_compiled;

/* The read-only context threaded through the matcher: the quirks-mode flag, the
   element :scope matches (the query root), and the tree text spans resolve against. */
typedef struct {
    th_tree *tree;
    th_node *scope;
    int quirks;
} sel_ctx;

typedef struct {
    Py_UCS4 *src; /* the owned source copy; sel_ident decodes escapes into it in place */
    Py_ssize_t pos;
    Py_ssize_t len;
    th_tree *tree;
    int error;
    int depth;              /* nesting level of the functional-pseudo lists being parsed */
    Py_ssize_t err_pos;     /* the position the failing token starts at, for the error message */
    const char *err_reason; /* a human-readable reason for the failure */
} sel_parser;

/* The deepest functional-pseudo nesting (:is()/:where()/:has()/:not(), and the 'of S'
   clause) the parser accepts. A recursive-descent parse of one level costs a bounded
   stack frame; the cap keeps a hostile, deeply nested selector from overflowing the C
   stack rather than raising a clean error (issue #421). Real selectors nest a handful
   of levels, far below this. */
#define SEL_MAX_DEPTH 128

/* Entry points compiled once in selector.c; the rest of the matcher and parser
   stays static there. query/methods.c drives the query bindings and to_xpath.c
   reuses the parser, so both units link against these. */
int sel_is_ident(Py_UCS4 ch);
Py_UCS4 sel_fold(Py_UCS4 ch, int ci);
int sel_eq(const Py_UCS4 *left, Py_ssize_t alen, const Py_UCS4 *right, Py_ssize_t blen, int ci);
int sel_same_type(const th_node *a, const th_node *b);
int sel_no_sibling(th_node *node, int from_end, int of_type);
const sel_simple *sel_single_simple(const sel_compiled *compiled);
int sel_match_simple(th_node *node, const sel_simple *simple, const sel_ctx *ctx);
sel_compiled *selector_compile(PyObject *selector_error, th_tree *tree, PyObject *selector_str);
void selector_free(sel_compiled *compiled);
int selector_matches(th_node *node, const sel_compiled *compiled, th_node *scope);
void sel_raise(PyObject *selector_error, PyObject *selector_str, const sel_parser *parser);
void sel_free_alts(sel_complex *alts, int count);
int sel_parse_alts(sel_parser *parser, sel_complex **out_alts, int *out_count, int nested, int relative, int forgiving);

#endif /* TURBOHTML_SELECTOR_H */
