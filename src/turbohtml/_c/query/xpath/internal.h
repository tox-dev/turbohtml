/* Shared internal contract for the XPath translation units: the arena AST, the token
   and evaluation-context structs, and the helpers the lexer/parser/evaluator/function
   library call across their split-file boundaries. */

#ifndef TURBOHTML_XPATH_INTERNAL_H
#define TURBOHTML_XPATH_INTERNAL_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <stdint.h>

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
    uint8_t kind;     /* enum xn_kind */
    uint8_t axis;     /* enum xp_axis (XN_STEP) */
    uint8_t test;     /* enum xp_test (XN_STEP) */
    uint8_t absolute; /* XN_PATH: a leading / was present */
    int32_t first;    /* first child / operand */
    int32_t second;   /* second child */
    int32_t next;     /* sibling chain: steps, predicates, args */
    double num;       /* XN_NUM */
    Py_UCS4 *str;     /* owned name/literal; NULL when none */
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
    int32_t root; /* index of the root expression */
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
   spine -- can consume before it faults (issue #421). */
#define XP_MAX_DEPTH 1024

/* The evaluation context: the tree, the current node, its 1-based proximity
   position and the context size, plus where to report an unimplemented feature. */
typedef struct {
    xp_live_frame *current;
    uint64_t next_id;
} xp_live_registry;

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
    struct xr_cache **regex_cache;
    xp_live_registry *live;
    xp_before_python_fn before_python;
    xp_name_test_fn name_test;
    void *name_test_ctx;
    int strict_no_ns;
} xp_ctx;

struct xp_live_frame {
    const xp_live_frame *previous;
    uint64_t id;
    uint64_t version;
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

static inline void xp_live_changed(xp_ctx *ctx) {
    if (ctx->live != NULL) {
        ctx->live->current->version++;
    }
}

static inline int xp_before_python(xp_ctx *ctx) {
    return ctx->before_python == NULL ? 0 : ctx->before_python(ctx->extension_ctx, ctx->live->current);
}

/* Pre-order successor, shared by the evaluator and id(). ns_push is declared in the
   public xpath.h because the marshaling boundary also builds node-sets through it. */
struct th_node *document_next(struct th_node *node);

/* The root node XPath's `/` selects for node: the tree's document (or parsed
   fragment root) when node sits under it, else the top of node's own subtree, the
   way DOM's getRootNode() answers for a node built outside any document. */
struct th_node *tree_root(struct th_tree *tree, struct th_node *node);

/* The XPath string-value of a node-set member, freshly allocated. */
Py_UCS4 *item_string(struct th_tree *tree, xp_item item, Py_ssize_t *len);
Py_UCS4 *ucs4_dup(const Py_UCS4 *src, Py_ssize_t len);
Py_UCS4 *ucs4_from_ascii(const char *src, Py_ssize_t length, Py_ssize_t *len);
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
int eval_expr(const xp_program *prog, int32_t idx, xp_ctx *ctx, xp_result *out);
int eval_function(const xp_program *prog, int32_t idx, xp_ctx *ctx, xp_result *out);
int xp_pattern_is_static(const xp_program *prog);
int xp_single_attribute_atom(const xp_program *prog, struct th_tree *tree, uint32_t *atom);

#endif /* TURBOHTML_XPATH_INTERNAL_H */
