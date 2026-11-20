/* Shared internal contract for the XPath translation units: the arena AST, the token
   and evaluation-context structs, and the helpers the lexer/parser/evaluator/function
   library call across their split-file boundaries. */

#ifndef TURBOHTML_XPATH_INTERNAL_H
#define TURBOHTML_XPATH_INTERNAL_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <stdint.h>

#include "core/fuzzing.h"
#include "query/xpath/xpath.h"

enum xn_kind {
    XN_OR,
    XN_AND,
    XN_EQ,
    XN_NE,
    XN_LT,
    XN_LE,
    XN_GT,
    XN_GE,
    XN_ADD,
    XN_SUB,
    XN_MUL,
    XN_DIV,
    XN_MOD,
    XN_NEG,    /* unary minus: a = operand */
    XN_UNION,  /* a | b */
    XN_NUM,    /* num literal */
    XN_LIT,    /* string literal: str/str_len */
    XN_VAR,    /* variable reference: str/str_len holds the name after '$' */
    XN_PATH,   /* absolute flag; a = first step; b = filter primary or -1 */
    XN_STEP,   /* axis/test/name; a = first predicate (XN_PRED chain); next = next step */
    XN_PRED,   /* predicate wrapper: a = expr; next = next predicate */
    XN_FUNC,   /* function call: str=name; a = first arg (chained by next) */
    XN_FILTER, /* FilterExpr: a = primary; b = predicate chain (applied to the whole set) */
};

enum xp_axis {
    AX_CHILD,
    AX_DESCENDANT,
    AX_DESCENDANT_OR_SELF,
    AX_ATTRIBUTE,
    AX_SELF,
    AX_PARENT,
    AX_ANCESTOR,
    AX_ANCESTOR_OR_SELF,
    AX_FOLLOWING_SIBLING,
    AX_PRECEDING_SIBLING,
    AX_FOLLOWING,
    AX_PRECEDING,
    AX_NAMESPACE,
};

enum xp_test {
    NT_NAME,    /* a QName/NCName name test; str holds the (copied) name */
    NT_STAR,    /* * */
    NT_NODE,    /* node() */
    NT_TEXT,    /* text() */
    NT_COMMENT, /* comment() */
    NT_PI,      /* processing-instruction(); str holds the optional literal target */
};

typedef struct {
    uint8_t kind;       /* enum xn_kind */
    uint8_t axis;       /* enum xp_axis (XN_STEP) */
    uint8_t test;       /* enum xp_test (XN_STEP) */
    uint8_t absolute;   /* XN_PATH: a leading / was present */
    uint8_t positional; /* XN_STEP and XN_PRED: a predicate may depend on the context's position, see parser.c;
                           inside an offset formula, the expression reads position() */
    uint8_t offsets;    /* XN_STEP: its positional predicates read offsets into shared walks (offset_formula) */
    int32_t first;      /* first child / operand */
    int32_t second;     /* second child */
    int32_t next;       /* sibling chain: steps, predicates, args */
    double num;         /* XN_NUM */
    Py_UCS4 *str;       /* owned name/literal; NULL when none */
    Py_ssize_t str_len;
    Py_ssize_t prefix_len; /* NT_NAME: length of the "prefix" before the ':' in str, else 0 */
} xn;

struct xp_program {
    size_t references;
    int has_python_calls;
    int has_variables;
    xn *nodes;
    int32_t count;
    int32_t cap;
    int32_t root;        /* index of the root expression */
    xp_namespaces bound; /* the prefix bindings xp_bind_namespaces kept, owned by the program */
};

/* Append a blank node, returning its index or -1 on OOM. */
int32_t xn_new(xp_program *prog, enum xn_kind kind);

typedef enum {
    TK_EOF,
    TK_SLASH,
    TK_DSLASH,
    TK_LBRACK,
    TK_RBRACK,
    TK_LPAREN,
    TK_RPAREN,
    TK_AT,
    TK_DOLLAR,
    TK_COMMA,
    TK_DOT,
    TK_DOTDOT,
    TK_PIPE,
    TK_COLONCOLON,
    TK_PLUS,
    TK_MINUS,
    TK_STAR,
    TK_EQ,
    TK_NE,
    TK_LT,
    TK_LE,
    TK_GT,
    TK_GE,
    TK_NUM,
    TK_LITERAL,
    TK_NAME,
    TK_AND,
    TK_OR,
    TK_DIV,
    TK_MOD,
} tok_kind;

typedef struct {
    const Py_UCS4 *src;
    Py_ssize_t len;
    Py_ssize_t pos;
    Py_ssize_t tokpos; /* start of the current token in src, for error positions */
    tok_kind kind;
    const Py_UCS4 *tstart; /* NAME / LITERAL text (into src) */
    Py_ssize_t tlen;
    Py_ssize_t tprefix; /* NAME: length of a "prefix:" before the local part, else 0 */
    double num;         /* NUM value */
    int op_context;     /* an operator may appear here (disambiguation state) */
    int error;          /* a lexical error was hit */
} lexer;

static inline int xp_is_space(Py_UCS4 ch) {
    return ch == ' ' || ch == '\t' || ch == '\r' || ch == '\n';
}

/* Advance the lexer past the current token, leaving the next one in lx->kind. */
void lex_next(lexer *lx);

/* Whether the lexer's current NAME/LITERAL text equals the ASCII keyword kw. */
int xp_name_eq(const lexer *lx, const char *kw);

/* The implicit xml namespace, the only namespace node an HTML tree exposes. */
#define XP_XML_NS_URI "http://www.w3.org/XML/1998/namespace"
#define XP_XML_NS_PREFIX "xml"

/* Foreign-content namespaces. HTML elements report no namespace (matching lxml's
   HTML parser, and keeping unprefixed name tests consistent); SVG and MathML
   subtrees carry the real URI the tree builder tagged them with. */
#define XP_SVG_NS_URI "http://www.w3.org/2000/svg"
#define XP_MATHML_NS_URI "http://www.w3.org/1998/Math/MathML"

/* An upper bound on parser nesting and evaluator recursion. A single group or
   operator level costs a bounded number of C stack frames, so this caps the stack a
   pathological expression -- deeply nested groups, or a long left-associative operator
   spine -- can consume before it faults (issue #421). A level is up to ~0.65 KiB of
   frames with clang -O2, ~0.95 KiB with gcc -O3 and LTO and 1.1 to 1.5 KiB unoptimized,
   so 64 of them take 40 to 60 KiB in an optimized build: under half of the 128 KiB
   stack a musl thread starts with, which leaves a Python callback made from the deepest
   level the margin CPython keeps, and an unoptimized build fits that stack too. The
   1024 levels this used to admit overflowed any thread stack below 2 MiB. The counter
   measures stack rather than syntax: what recurses through more frames than one level's
   charges the extra levels below. It is not a TH_DEPTH_LIMIT: a tenth of it is six
   levels, fewer than a predicate inside a predicate or the class test css_to_xpath
   writes takes, and 64 levels already fire on an expression of a hundred characters. */
#define XP_MAX_DEPTH 64

/* Levels past this one also compare their frame with the thread's stack floor (xp_too_deep): 64 levels fit the default
   thread stacks, but not a thread sized near the smallest Python starts. Shallower levels pay one comparison. */
#define XP_STACK_CHECK_DEPTH 16

/* Whether a level at depth reaches XP_MAX_DEPTH or the end of the thread's C stack. */
int xp_too_deep(int depth);

/* How deep a position test may nest for the evaluator to work it out from the size of a context's stretch alone
   (offset_formula in parser.c, view_positions in eval.c). */
#define XP_OFFSET_DEPTH 8

/* What the evaluator charges on top of the level eval_expr counts. A function call
   keeps its frame under its arguments and a location path its step state under its
   base expression and its predicates (XP_HELD_DEPTH), and a predicate its context
   under its expression (XP_PREDICATE_DEPTH): each weighs about one more level. The
   charge is a constant in the guard of the expression evaluated under that frame, so
   no call or path pays for it separately. */
#define XP_HELD_DEPTH 1
#define XP_PREDICATE_DEPTH 1

/* The evaluation context: the tree, the current node, its 1-based proximity
   position and the context size, plus where to report an unimplemented feature. */
typedef struct {
    xp_live_frame *current;
    uint64_t next_id;
} xp_live_registry;

/* What one evaluation caches (eval.c): compiled regexes and the sibling positions the document-order sort numbered.
   Allocated on first use, shared by the predicate contexts, and freed when the evaluation ends. */
typedef struct xp_caches xp_caches;

/* Drop the sibling positions once Python runs, since Python can move nodes. */
void xp_order_forget(xp_caches *caches);

/* The root `/` and id() start from, found once per top-level evaluation instead of climbed to from every predicate's
   context node. Outside a parsed fragment (see tree_root) every node an axis reaches has the root of the node the
   evaluation started at. A variable, or an extension function that can return or move nodes, can bring in a node of
   another subtree, so from then on `mixed` is set and every context node is climbed from again, through a memo past
   the first ancestors (climb_memoized). The XSLT engine evaluates over its source tree only, which nothing edits during
   a transform, so the root it passes is `fixed`. */
typedef struct {
    struct th_node *node; /* NULL until found, unless the caller of xp_eval_at supplied it */
    int mixed;
    int fixed;
    xp_id_cache *ids; /* the caller's id() cache, or NULL to use own */
    xp_id_cache own;  /* the evaluation's id() cache, cleared when it ends */
    int want_object;  /* the root's string result goes to Python, so it may come back as an XP_PYSTRING */
} xp_root;

typedef struct {
    struct th_tree *tree;
    struct th_node *node;
    Py_ssize_t attr; /* the context is node itself at -1, else an encoded attribute or namespace node (-2) */
    Py_ssize_t pos;
    Py_ssize_t size;
    const char **feature;
    const xp_bindings *vars;
    const xp_namespaces *namespaces;
    xp_extension_fn extension;
    void *extension_ctx;
    int depth; /* current eval_expr recursion depth, capped at XP_MAX_DEPTH */
    xp_caches **caches;
    xp_live_registry *live;
    xp_before_python_fn before_python;
    xp_name_test_fn name_test;
    void *name_test_ctx;
    int strict_no_ns;
    xp_root *root;        /* shared by the predicate contexts of one top-level evaluation */
    xp_lang_memo **langs; /* the caller's lang() memo (xp_eval_at), or NULL for the evaluation's own in caches */
#ifdef TH_OPERATION_LIMIT
    size_t *operations; /* shared by the predicate contexts of one top-level evaluation */
#endif
} xp_ctx;

struct xp_live_frame {
    const xp_live_frame *previous;
    uint64_t id;
    struct th_node *node;
    const xp_nodeset *sets[2];
    const xp_nodeset *append;
    const xp_result *results;
    Py_ssize_t result_count;
    const xp_bindings *vars;
};

static inline void xp_live_enter(xp_ctx *ctx, xp_live_frame *frame) {
    if (ctx->live != NULL) {
        frame->previous = ctx->live->current;
        frame->id = ++ctx->live->next_id;
        ctx->live->current = frame;
    }
}

static inline void xp_live_leave(xp_ctx *ctx, xp_live_frame *frame) {
    if (ctx->live != NULL) {
        ctx->live->current = (xp_live_frame *)frame->previous;
    }
}

static inline int xp_before_python(xp_ctx *ctx) {
    if (ctx->before_python == NULL) {
        return 0;
    }
    xp_order_forget(*ctx->caches);
    return ctx->before_python(ctx->extension_ctx, ctx->live->current);
}

/* The evaluation's regex cache slot, or NULL with MemoryError set. */
struct xr_cache *xp_regex_cache(xp_ctx *ctx);

#ifdef TH_OPERATION_LIMIT
/* Fuzz builds pass -DTH_OPERATION_LIMIT so a super-linear expression stops with a ValueError instead of a timeout that
   hides other findings. libxml2 charges each evaluated operation and each node a step yields the same way
   (xpath.c xmlXPathCheckOpLimit); release builds compile none of this. */
static inline int xp_charge(xp_ctx *ctx, size_t count) {
    *ctx->operations += count;
    if (*ctx->operations <= TH_OPERATION_LIMIT) {
        return 0;
    }
    *ctx->feature = "evaluation exceeded the operation limit";
    return -3;
}
#endif

/* Pre-order successor, shared by the evaluator and id(). ns_push is declared in the
   public xpath.h because the marshaling boundary also builds node-sets through it. */
struct th_node *document_next(struct th_node *node);

/* The document order the evaluation's sort uses (eval.c), also for the functions that read a node-set member by
   member. A caller shares xp_order_budget(len) steps across one set of len items; xp_node_order answers negative when
   left precedes right and positive otherwise (the two are never the same node), and XP_ORDER_UNKNOWN once the walks
   would spend more or the nodes lie in separate trees, which no link orders. A set whose order the links settle only
   after long walks (a wide sibling list, a deep chain) is ranked once instead. xp_first_out_of_order ranks one: it
   gives the first node item after start that does not follow the node item before it, ignoring repeats of that node,
   else len, or -1 with MemoryError. */
#define XP_ORDER_UNKNOWN 2

static inline Py_ssize_t xp_order_budget(Py_ssize_t len) {
    return 64 * len + 256;
}

int xp_node_order(xp_caches **caches, struct th_node *left, struct th_node *right, Py_ssize_t *budget);
Py_ssize_t xp_first_out_of_order(xp_caches **caches, const xp_nodeset *set, Py_ssize_t start);

/* The root node XPath's `/` selects for the context node: the tree's document (or parsed
   fragment root) when the node sits under it, else the top of the node's own subtree, the
   way DOM's getRootNode() answers for a node built outside any document. */
struct th_node *tree_root(xp_ctx *ctx);

/* The attribute declaring node's own language for lang() (functions.c), or NULL. */
const th_node_attr *xp_node_lang_attr(struct th_tree *tree, struct th_node *node, uint32_t xml_lang_atom);

/* The attribute declaring the language of `from`, read on its nearest self-or-ancestor element that carries one, or
   NULL: lang()'s climb past its first ancestors (eval.c). */
const th_node_attr *xp_lang_memoized(xp_ctx *ctx, struct th_node *from, uint32_t xml_lang_atom);

/* The XPath string-value of a node-set member, freshly allocated. */
Py_UCS4 *item_string(struct th_tree *tree, xp_item item, Py_ssize_t *len);
Py_UCS4 *ucs4_dup(const Py_UCS4 *src, Py_ssize_t len);
Py_UCS4 *ucs4_from_ascii(const char *src, Py_ssize_t length, Py_ssize_t *len);
Py_UCS4 *ucs4_from_utf8(const char *src, Py_ssize_t length, Py_ssize_t *len);
double parse_number(const Py_UCS4 *text, Py_ssize_t len);
/* The correctly rounded value of a decimal run of ASCII digits with at most one '.' and
   at least one digit (XPath's Number production, which the lexer and parse_number
   validate first). */
double xp_decimal_value(const Py_UCS4 *digits, Py_ssize_t len);

/* Result constructors and the XPath type conversions shared by eval.c and functions.c. */
void result_bool(xp_result *result, int value);
void result_number(xp_result *result, double value);
void result_string(xp_result *result, Py_UCS4 *owned, Py_ssize_t len);
int to_boolean(struct th_tree *tree, const xp_result *value);
Py_UCS4 *to_string(struct th_tree *tree, const xp_result *value, Py_ssize_t *len);
double to_number(struct th_tree *tree, const xp_result *value);

/* The mutual recursion between the evaluator and the function library. */
int eval_guarded(const xp_program *prog, int32_t idx, xp_ctx *ctx, xp_result *out, int exists);
static inline int eval_expr(const xp_program *prog, int32_t idx, xp_ctx *ctx, xp_result *out) {
    return eval_guarded(prog, idx, ctx, out, 0);
}
/* eval_expr for an expression whose value is only tested: as a predicate, or through to_boolean(). A location path
   then stops at the first node it selects, so a node-set in *out is empty exactly when the whole one is and holds
   nodes of it, but not necessarily all of them. */
static inline int eval_condition(const xp_program *prog, int32_t idx, xp_ctx *ctx, xp_result *out) {
    return eval_guarded(prog, idx, ctx, out, 1);
}
/* eval_guarded for a function argument or a path base, charged XP_HELD_DEPTH for the frame beneath it. */
int eval_held(const xp_program *prog, int32_t idx, xp_ctx *ctx, xp_result *out, int exists);
int eval_function(const xp_program *prog, int32_t idx, xp_ctx *ctx, xp_result *out);
int xp_pattern_is_static(const xp_program *prog);
int xp_pattern_reads_call(const xp_program *prog);
int xp_step_reads_position(const xp_program *prog, int32_t step_index);
int xp_single_attribute_atom(const xp_program *prog, struct th_tree *tree, uint32_t *atom);
/* The number of nodes count() of the path at path_idx selects from the context, answered from the walk memo, or 0 with
   nothing counted when the memo does not apply. */
int xp_count_walk(const xp_program *prog, int32_t path_idx, xp_ctx *ctx, Py_ssize_t *count);

#endif /* TURBOHTML_XPATH_INTERNAL_H */
