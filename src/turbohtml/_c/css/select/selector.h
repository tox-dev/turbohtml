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
#include "core/fuzzing.h"
#include "core/node_map.h"
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
    PSEUDO_SUBSTR_LONG,
    /* never parsed: the lone simple of the stand-in sel_deep_enter hands a query, whose sub is the real selector list
     */
    PSEUDO_DEEP,
    PSEUDO_DEEP_LISTS, /* the stand-in's simple for a selector of SEL_CLIMBS_LISTS */
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
    /* ':' with a sub, set by sel_alts_nest: 0 when one match asks it about an element once; otherwise the deep matcher
       keeps its answers: SEL_NEST_ALWAYS or SEL_NEST_DEEP for :is(), :where() and :not(), and for an 'of S' clause its
       1-based number among the selector's clauses */
    int nest;
} sel_simple;

typedef struct {
    sel_simple *simples;
    int count;
    char combinator; /* ' ', '>', '+', '~': the combinator joining the compound on the left */
} sel_compound;

/* What sel_alts_nest finds a list's walks reach: only ancestors, a number the tree's depth bounds, or siblings and
   subtrees, which it does not */
#define SEL_WALKS_UP 1
#define SEL_WALKS_WIDE 2

/* sel_simple.nest of an :is(), :where() or :not() asked again: its walks reach wide or ask a list again themselves,
   or only climb, so the direct walk costs no more than the tree nests and wins on a tree known to nest shallow */
#define SEL_NEST_DEEP 1
#define SEL_NEST_ALWAYS 2

/* sel_complex.walks bits, set by sel_alts_nest on the alternatives of a nested list */
#define SEL_ALT_WALKS 1  /* matching it can walk: a combinator, :has(), :lang(), :dir(), or a list that walks */
#define SEL_ALT_SCOPED 2 /* it writes :scope outside a :has(), so its answer depends on the scope element */
#define SEL_ALT_AHEAD                                                                                                  \
    4 /* it writes :scope in the S of an :nth-last-child(), which reads the siblings after an element */

struct sel_complex {
    sel_compound *compounds; /* left to right; matched from the rightmost (the subject) */
    int count;
    int walks; /* SEL_ALT_WALKS | SEL_ALT_SCOPED | SEL_ALT_AHEAD, for an alternative of a nested list */
};

typedef struct {
    Py_UCS4 *source; /* an owned copy of the selector text the slices point into */
    sel_complex *alts;
    int count;
    uint16_t subject_atom; /* 0 before index preparation, UINT16_MAX without a common subject tag */
    uint16_t left_atom;
    int quirks;         /* the tree was parsed in quirks mode: class/ID match case-insensitively */
    int has_relational; /* the selector contains a :has() somewhere: a match may want the subtree memo */
    int nests_has;      /* a :has() sits inside another :has(): a single match wants the sibling table */
    int reads_legend;   /* a :disabled, :enabled, :read-only or :read-write somewhere: a match wants the legend memo */
    int reads_dir;      /* a :dir() somewhere: a walk over many nodes wants one context for its dir=auto answers */
    th_tree *tree;      /* the tree the selector runs on; :empty and :dir(auto) read text spans through it */
    /* 1 when a match walks ancestors, as far as the tree nests: a descendant combinator, :lang() or :dir() somewhere;
       2 when a match walks ancestors from each ancestor such a walk passes, a square of how far the tree nests */
    int climbs;
    /* the most compounds a match recurses through: one complex selector's compounds plus the longest chain of a
       functional pseudo-class list nested in them (sel_alts_chain) */
    Py_ssize_t chain;
} sel_compiled;

/* sel_compiled.climbs of a selector with a nested list one match asks again (sel_simple.nest): it runs on the deep
   matcher on any tree, which keeps those answers */
#define SEL_CLIMBS_LISTS 3

/* One memoized bit: key names the question (a :has() argument, or the compound a general
   sibling combinator looks for) and node is the element it was answered for, or for a class
   or attribute test on a long value, the value buffer. seq, the element's seq or
   th_node_values_seq for a value, picks the home slot and lets the table rehash without
   reading node. key == NULL marks an empty slot. */
typedef struct {
    const void *key;
    const void *node;
    uint32_t seq;
    unsigned char result;
} sel_memo_slot;

/* A class, ~= or *= test on an attribute value at least this long goes through the value memo
   (sel_ctx.value_memo); a shorter value costs less to read than a probe. */
#define SEL_VALUE_MEMO_MIN 256

/* How the last general-sibling walk for one compound ended: the compound it looked for,
   the node it started from and its match status. */
typedef struct {
    const void *key;
    const th_node *node;
    int status;
} sel_sibling_record;

/* One record per combinator position (mod this count), so the nested walks of a chain such
   as a ~ b ~ c keep their own. */
#define SEL_SIBLING_RECORDS 4

/* A per-query open-addressing memo of (key, node) answers that hold for the whole query,
   so a driver walking many candidates computes each answer once. The :has() memo turns the
   O(n^2) subtree re-walk into a single amortized-linear pass; the sibling memo does the
   same for the sibling walks of the general sibling combinator and :has(~ ...). The two
   key by different objects (a relative selector, a compound), so a driver can hand one
   table to both. Owned by the driver loop, threaded read/write through sel_ctx; slots ==
   NULL until the first insert, so a query that never stores pays nothing. A query's table
   serves one selector and is freed with the query. An owner that keeps one table across
   many selectors sets limit: every :has() argument keys its own answers, so the table
   would otherwise grow to arguments x elements. */
typedef struct {
    sel_memo_slot *slots;
    size_t mask; /* capacity - 1 (capacity a power of two); 0 while slots == NULL */
    size_t count;
    int failed;   /* growth failed or hit limit: fall back to the direct walk */
    size_t limit; /* the largest capacity the table may grow to; 0 for no bound */
} sel_memo;

typedef struct {
    sel_memo table;
    /* A query in document order reaches the node the last walk started from before any
       entry of the table, so the sibling walks stop there. */
    sel_sibling_record last_walks[SEL_SIBLING_RECORDS];
} sel_sibling_memo;

typedef struct {
    th_node *node;
    th_node *scope;
    const sel_simple *simple;
    int index;
} sel_nth_memo;

typedef struct {
    th_node *form;
    th_node *first;
} sel_default_memo;

/* Sibling positions, which hold for the whole query: index[from_end][of_type] maps an
   element to its 1-based position among its element siblings, counted from the last one
   when from_end and over the siblings of its own type when of_type. sel_nth_memo follows a
   walk only from one sibling to the next of its type, and :first-of-type, :last-of-type and
   :only-of-type walk until they meet a sibling of the candidate's type, so a long sibling
   list that mixes types (each custom tag name is a type of its own) costs one walk per
   candidate, O(n^2) for the list. A map takes a sibling list whole the first time a walk
   over it runs long; a query whose walks all stay short allocates nothing. */
typedef struct {
    th_node_map index[2][2];
} sel_position_memo;

/* Single-element matching has no query walk to reuse, so the other memo pointers can be NULL. The
   sibling memo assumes one scope element for its whole life: a driver that rebinds scope
   per candidate passes it only for a selector without :scope. The legend memo is set by every
   driver, and by a single test only for a selector that reads a :disabled-family pseudo-class
   (sel_compiled.reads_legend). It holds the :disabled answers that are facts of the tree alone
   (which legends are their fieldset's first, and whether a fieldset above a node disables it), so
   it can share a table with the other two and outlive a scope, for as long as the tree is not
   mutated. It also holds the first strong direction of the nodes a :dir() walk entered, the same
   kind of fact; when a context has no legend memo, the first such walk points legend_memo at a
   per-thread table. The value memo is never NULL: it holds what a class, ~= or *= test found
   in a long attribute value, a fact of the value alone, so a driver can share it across scopes
   and hand it the table it gives the legend memo. */
typedef struct {
    th_tree *tree;
    th_node *scope;
    int quirks;
    sel_memo *has_memo;
    sel_nth_memo *nth_memo;
    sel_default_memo *default_memo;
    sel_sibling_memo *sibling_memo;
    sel_memo *legend_memo;
    sel_position_memo *position_memo;
    sel_memo *value_memo;
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
    Py_ssize_t compounds;   /* every compound parsed, nested lists included: no chain is longer than this */
} sel_parser;

/* The deepest functional-pseudo nesting (:is()/:where()/:has()/:not(), and the 'of S'
   clause) the parser accepts. A recursive-descent parse of one level costs a bounded
   stack frame; the cap keeps a hostile, deeply nested selector from overflowing the C
   stack rather than raising a clean error (issue #421). Real selectors nest a handful
   of levels, far below this. */
#define SEL_MAX_DEPTH TH_DEPTH_LIMIT(128)

/* A selector whose chain is at most this long matches without checking the C stack: its recursion fits in the margin
   core/stack.h keeps below every checked frame. */
#define SEL_CHAIN_CHECK 64

/* Whether the calling thread's C stack holds a match that recurses through chain compounds. Returns 0, or -1 with
   RecursionError set. */
int sel_stack_check_chain(Py_ssize_t chain);

/* A driver asks this once per call before it matches a selector of this chain, since a compiled selector may be cached
   on one thread and matched on another. */
static inline int sel_stack_check(Py_ssize_t chain) {
    return chain > SEL_CHAIN_CHECK ? sel_stack_check_chain(chain) : 0;
}

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
int selector_matches(th_node *node, const sel_compiled *compiled, th_node *scope, sel_memo *value_memo);
int selector_matches_c(th_node *node, const sel_compiled *compiled, const sel_ctx *ctx);
/* Match one comma-separated alternative (a single complex selector) against node,
   so the cascade can pair each matching alternative with its own specificity. */
int selector_matches_alt(th_node *node, const sel_complex *complex, const sel_ctx *ctx);
/* Accumulate the Selectors-4 §17 (a, b, c) specificity of one complex selector into
   the three out-parameters (they must start zeroed). Shared by _css_specificity and
   the CSSOM cascade. */
void sel_specificity(const sel_complex *complex, int *spec_a, int *spec_b, int *spec_c);
int selector_uses_has_memo(const sel_compiled *compiled);
/* Whether the selector writes :scope anywhere, so a match depends on the scope element. */
int selector_uses_scope(const sel_compiled *compiled);
void sel_memo_free(sel_memo *memo);
/* An answer about one of node's long attribute value buffers, kept in a value memo under a key that names the
   question; every element sharing the buffer finds it. */
int sel_value_memo_get(const sel_memo *memo, const void *key, const th_node *node, const Py_UCS4 *value, int *out);
void sel_value_memo_put(sel_memo *memo, const void *key, const th_node *node, const Py_UCS4 *value, int result);
void sel_position_memo_free(sel_position_memo *memo);
void sel_raise(PyObject *selector_error, PyObject *selector_str, const sel_parser *parser);

/* The answers of an 'of S' clause the deep matcher keeps: each element sibling's count of the earlier siblings that
   match S, plus one. scope is the scope element they were counted under when S writes :scope. */
typedef struct {
    th_node_map map;
    const th_node *scope;
    const sel_simple
        *owner; /* the clause: one memo serves every rule of the cascade, whose clauses each number from 1 */
} sel_deep_counts;

/* The deep stand-in's answers: two bits (unknown, no, yes) per key and node, a key being a pointer into the selector
   (a :has() compound index, an ancestor target or a simple). A key's pages are indexed by node->seq, which the
   tree numbers from 0 in creation order, and a page exists once a node in its block is stored. The memo holds
   at most limit bytes of pages and directories, and past that, or when allocation fails, it is failed and the
   matcher walks directly. */
typedef struct {
    const void *key;       /* NULL marks an empty slot */
    unsigned char **pages; /* a page per block of SEL_DEEP_PAGE_NODES seqs, NULL until one of its nodes is stored */
    size_t pages_len;
} sel_deep_key;

typedef struct sel_deep_kept sel_deep_kept;

typedef struct {
    sel_deep_key *keys; /* open addressing by key, NULL until the first store */
    size_t mask;        /* capacity - 1 */
    size_t count;       /* keys stored */
    sel_deep_key *last; /* the slot of the key looked up last, valid until the table grows */
    size_t bytes;       /* bytes of pages and page directories allocated */
    size_t limit;       /* the most bytes of pages and directories a caller sets */
    int failed;         /* past the limit or out of memory: the matcher walks directly */
    int subject_scope;  /* the driver binds :scope to the element it tests, copied into kept when it is allocated */
    sel_deep_kept
        *kept; /* the tables past the ancestor memo, allocated by the first match that needs one, on the heap */
} sel_deep_memo;

/* The tables of the deep matcher past the ancestor memo. lists keeps the answers of the nested lists one match asks
   again (sel_simple.nest): whether an alternative that walks matches an element and whether a :has() holds for one,
   which hold under any scope; scoped keeps the answers that depend on the scope element, for scoped_for alone. */
struct sel_deep_kept {
    sel_deep_memo lists;
    sel_deep_memo scoped;
    const th_node *scoped_for;
    /* the driver binds :scope to the element it tests (the cascade, matches(), Matcher.filter), so an answer about any
       other element the match reaches holds under every scope, unless it reads siblings after the element */
    int subject_scope;
    sel_deep_counts **counts; /* open addressing by clause, NULL until a clause counts */
    int count_slots;          /* capacity */
    int count_used;
    sel_position_memo positions; /* sibling positions inside an 'of S' clause, for a context that has none */
};

void sel_deep_memo_free(sel_deep_memo *memo);

/* A query's stand-in for a selector that climbs (sel_compiled.climbs) on a tree that may nest deep. The HTML tree
   builder nests no deeper than TH_MAX_TREE_DEPTH, but an XML or a built tree nests without limit, and an ancestor walk
   from every candidate of a deep one is quadratic. The stand-in is one compound of one PSEUDO_DEEP simple whose sub is
   the selector's list: the drivers run it like any selector, and the simple runs the list through the matcher that
   keeps the answers of its ancestor walks in memo, so a matcher built for shallow trees pays nothing for deep ones. The
   memo holds for one scope and one state of the tree, like the driver's sibling memo. */
typedef struct {
    sel_compiled compiled;
    sel_complex complex;
    sel_compound compound;
    sel_simple simple;
    sel_deep_memo memo;
} sel_deep_query;

sel_compiled *sel_deep_wrap(sel_compiled *compiled, sel_deep_query *deep);
/* Match one alternative as selector_matches_alt does, keeping the answers of the ancestor walks in memo. */
int sel_deep_matches_alt(th_node *node, const sel_complex *complex, const sel_ctx *ctx, sel_deep_memo *memo);

/* Whether a query over compiled runs on a stand-in: compiled climbs on a tree that may nest deep, or climbs from its
   climbs on any tree. */
static inline int sel_climbs_deep(const sel_compiled *compiled) {
    return compiled->climbs > 1 || (compiled->climbs == 1 && th_tree_nests_deep(compiled->tree));
}

/* selector_matches(node, compiled, scope, NULL) on the stand-in of compiled, for a caller that checked
   sel_climbs_deep. */
int selector_matches_deep(th_node *node, sel_compiled *compiled, th_node *scope);

/* The selector a query over compiled runs: compiled itself, or the stand-in in deep when sel_climbs_deep.
   sel_deep_leave frees what the stand-in kept. */
static inline sel_compiled *sel_deep_enter(sel_compiled *compiled, sel_deep_query *deep) {
    deep->compiled.alts = NULL;
    return compiled->climbs != 0 && sel_climbs_deep(compiled) ? sel_deep_wrap(compiled, deep) : compiled;
}

static inline void sel_deep_leave(sel_deep_query *deep) {
    if (deep->compiled.alts != NULL) {
        sel_deep_memo_free(&deep->memo);
    }
}
void sel_free_alts(sel_complex *alts, int count);
int sel_parse_alts(sel_parser *parser, sel_complex **out_alts, int *out_count, int nested, int relative, int forgiving);

#endif /* TURBOHTML_SELECTOR_H */
