/* XPath 1.0 over the turbohtml DOM (issue #179).

   This header is the seam between the engine and the rest of the module. The
   engine compiles a query string to an immutable program (a flat arena of tagged
   AST ops) once, then evaluates it many times against trees; the compiled program
   holds no tree pointers and no mutable state, so it is shareable across threads
   and lives in a per-handle cache like the CSS selector engine's.

   Phase 1 lands the front end only: the lexer and recursive-descent parser that
   build the arena AST, plus xp_dump for the conformance hook that the parser tests
   diff against. Evaluation and the Python xpath()/xpath_one() surface follow in
   later phases. */

#ifndef TURBOHTML_XPATH_H
#define TURBOHTML_XPATH_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <stdint.h>

#include "dom/tree.h"

typedef struct xp_program xp_program;

/* Compile an XPath expression given as code points. Returns the program, or NULL
   with a NUL-terminated message in errbuf (errlen is its capacity). A depth limit
   also raises depth_error. The source is not retained; names and literals are copied. */
xp_program *xp_compile(const Py_UCS4 *src, Py_ssize_t len, char *errbuf, size_t errlen, PyObject *depth_error);

/* Whether a name compiles back to a name test for exactly that name: Element.xpath_path asks before writing a tag. */
int xp_is_plain_name(const Py_UCS4 *text, Py_ssize_t len);

int xp_calls_python(const xp_program *prog);
void xp_retain(xp_program *prog);
void xp_free(xp_program *prog);

/* Render the compiled AST as a canonical S-expression (UCS4 code points), the form
   the parser tests assert against. PyMem-allocated; *out_len receives the length.
   NULL on allocation failure. */
Py_UCS4 *xp_dump(const xp_program *prog, Py_ssize_t *out_len);

/* Attribute snapshots share the index field to keep result items two words. */
struct th_node;
typedef struct {
    struct th_node *node;
    Py_ssize_t attr;
} xp_item;

typedef struct {
    xp_item *items;
    Py_ssize_t len;
    Py_ssize_t cap;
    int snapshots;
} xp_nodeset;

static inline int xp_is_attribute(Py_ssize_t attr) {
    return attr != -1 && attr != -2;
}

const th_node_attr *xp_item_attribute(struct th_tree *tree, xp_item item);
const char *xp_item_attr_name(struct th_tree *tree, xp_item item, Py_ssize_t *len);
int xp_item_equal(xp_item left, xp_item right);
Py_ssize_t xp_item_rank(Py_ssize_t attr);
uintptr_t xp_item_hash(xp_item item);

typedef struct xp_live_frame xp_live_frame;
typedef int (*xp_before_python_fn)(void *ctx, const xp_live_frame *frame);
const xp_live_frame *xp_live_previous(const xp_live_frame *frame);
uint64_t xp_live_id(const xp_live_frame *frame);
Py_ssize_t xp_live_append_count(const xp_live_frame *frame);
int xp_visit_frame_append(const xp_live_frame *frame, Py_ssize_t start, int (*visitor)(void *, struct th_node *),
                          void *data);
Py_ssize_t xp_live_result_count(const xp_live_frame *frame);
int xp_visit_frame_results(const xp_live_frame *frame, Py_ssize_t start, int (*visitor)(void *, struct th_node *),
                           void *data);
int xp_visit_frame_nodes(const xp_live_frame *frame, int (*visitor)(void *, struct th_node *), void *data);

void xp_nodeset_free(xp_nodeset *ns);

/* The marshaling boundary also builds node-set variables from caller-supplied elements. */
int ns_push(xp_nodeset *ns, struct th_node *node, Py_ssize_t attr);
int ns_grow(xp_nodeset *ns);

/* An append that skips the attribute snapshot reference ns_push takes. Only for items no snapshot can own, such as
   the source-tree nodes an XSLT transform collects, which are never tagged snapshot attributes. */
static inline int ns_append(xp_nodeset *ns, struct th_node *node, Py_ssize_t attr) {
    if (ns->len == ns->cap && ns_grow(ns) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                               /* GCOVR_EXCL_LINE */
    }
    ns->items[ns->len++] = (xp_item){node, attr};
    return 0;
}

/* The four XPath 1.0 value types an expression can evaluate to. */
enum xp_result_kind { XP_NODESET, XP_NUMBER, XP_STRING, XP_BOOLEAN };

typedef struct {
    enum xp_result_kind kind;
    xp_nodeset nodes; /* XP_NODESET (document-ordered, duplicate-free) */
    double number;    /* XP_NUMBER */
    Py_UCS4 *string;  /* XP_STRING, owned */
    Py_ssize_t string_len;
    int boolean; /* XP_BOOLEAN */
} xp_result;

void xp_result_free(xp_result *result);

/* A $name variable binding. The value is borrowed for the duration of one eval
   (the caller owns it and frees it afterwards); a referenced variable copies it. */
typedef struct {
    const Py_UCS4 *name;
    Py_ssize_t name_len;
    xp_result value;
} xp_binding;

typedef struct {
    const xp_binding *items;
    Py_ssize_t len;
} xp_bindings;

/* A prefix-to-URI binding for a name test. The strings are borrowed for the duration
   of one eval (the caller owns the code-point buffers and frees them afterwards). */
typedef struct {
    const Py_UCS4 *prefix;
    Py_ssize_t prefix_len;
    const Py_UCS4 *uri;
    Py_ssize_t uri_len;
} xp_namespace;

typedef struct {
    const xp_namespace *items;
    Py_ssize_t len;
} xp_namespaces;

/* Resolves a prefix a program's name tests use to the namespace URI its scope binds it to. Returns 1 with uri and
   uri_len set, 0 when the prefix is unbound, or -1 with a Python error set. */
typedef int (*xp_prefix_resolve_fn)(void *context, const Py_UCS4 *prefix, Py_ssize_t prefix_len, const Py_UCS4 **uri,
                                    Py_ssize_t *uri_len);

/* Give the program the bindings for the prefixes its prefixed name tests use, resolved once through resolve and
   copied into the program, so it carries its stylesheet's namespace scope. A program with no prefixed name test, or
   whose prefixes are all unbound, keeps none. Replaces any earlier binding. Returns 0, or -1 with a Python error
   set. */
int xp_bind_namespaces(xp_program *prog, xp_prefix_resolve_fn resolve, void *context);

/* A user extension-function call: resolve `name` against the registered
   extensions and run it over `args` with the context item (`context.node`, or the
   attribute or namespace node `context.attr` names) as the XPath context.
   Returns 0 with *out filled, -2 when no extension has that name (the engine then
   reports an unknown function), -1 on a Python error (the exception is set). */
struct th_node;
typedef int (*xp_extension_fn)(void *ctx, xp_item context, const Py_UCS4 *name, Py_ssize_t name_len,
                               const xp_result *args, int argc, xp_result *out);

/* Evaluate a compiled program against a context node. vars supplies $name bindings
   (NULL when none); namespaces supplies prefix-to-URI bindings for prefixed name tests
   (NULL when none); extension/extension_ctx supply user functions (extension NULL when
   none). Returns 0 with *out filled (the caller frees it with xp_result_free). Returns
   -3 for an evaluation error that maps to ValueError (a reference to an unbound variable,
   or a name test whose prefix is not bound), -4 for a type error that maps to TypeError
   (a value used where a node-set is required), or -5 for an expression nested past
   XP_MAX_DEPTH or the thread's C stack, which maps to XPathDepthError (a ValueError subclass),
   with *feature set to a short name for the message. Returns -1 on allocation failure or
   when a Python error is already set (a malformed regex, an extension failure, or an
   unknown-function/wrong-arity call reported with the function name). */
struct th_tree;
int xp_eval(const xp_program *prog, struct th_tree *tree, struct th_node *context, const xp_bindings *vars,
            const xp_namespaces *namespaces, xp_extension_fn extension, void *extension_ctx, xp_result *out,
            const char **feature);

int xp_eval_snapshot(const xp_program *prog, struct th_tree *tree, struct th_node *context, const xp_bindings *vars,
                     const xp_namespaces *namespaces, xp_extension_fn extension, void *extension_ctx,
                     xp_before_python_fn before_python, xp_result *out, const char **feature);

/* What id() keeps from one lookup to the next: the root its last unindexed lookup walked, and the index of the
   elements under a root by id that a second lookup there builds. The index records the tree's id version
   (th_tree_id_version) it was built at and is rebuilt once it moves, so it outlives Python code that leaves the tree
   alone. */
typedef struct xp_id_index xp_id_index;
typedef struct {
    struct th_node *walked;
    xp_id_index *index;
} xp_id_cache;
void xp_id_cache_clear(xp_id_cache *cache);

/* The language each node a deep lang() climb passed inherits (eval.c). */
typedef struct xp_lang_memo xp_lang_memo;
void xp_lang_memo_free(xp_lang_memo *memo);

/* Like xp_eval, but seeds the context item, position and size instead of the node with 1/1. context is a node, or
   an attribute or namespace node of one (context->attr, see xp_item). The XSLT engine reuses this so position()/last()
   at the top of a select or test expression report the item's place in the current node list (xslt.c). known_root is
   NULL, or the root `/` selects for context and for every other node the evaluation can meet, through vars and
   extension included: the engine evaluates once per instruction and node, all of them under its source root, so it
   passes that rather than have each evaluation climb to it. langs is NULL, or a slot holding NULL or a memo that
   outlives the call and that the caller ends with xp_lang_memo_free, for the same reason: lang() then climbs each
   ancestor about once per run rather than once per evaluation. ids is NULL, or a zeroed cache the caller owns
   and ends with xp_id_cache_clear, so the id() index outlives the call: the engine indexes its source once
   per run rather than walk it once per node. */
int xp_eval_at(const xp_program *prog, struct th_tree *tree, const xp_item *context, struct th_node *known_root,
               Py_ssize_t pos, Py_ssize_t size, const xp_bindings *vars, const xp_namespaces *namespaces,
               xp_extension_fn extension, void *extension_ctx, xp_lang_memo **langs, xp_id_cache *ids, xp_result *out,
               const char **feature);

/* xp_eval_at for an expression the caller only converts to a boolean, as the test of xsl:if and xsl:when is: a
   location path stops at the first node it selects rather than collecting them all, so a node-set in *out is empty
   exactly when the whole one is, and is good for nothing but that question. */
int xp_eval_condition_at(const xp_program *prog, struct th_tree *tree, const xp_item *context,
                         struct th_node *known_root, Py_ssize_t pos, Py_ssize_t size, const xp_bindings *vars,
                         const xp_namespaces *namespaces, xp_extension_fn extension, void *extension_ctx,
                         xp_lang_memo **langs, xp_id_cache *ids, xp_result *out, const char **feature);

typedef int (*xp_name_test_fn)(void *context, struct th_node *node, Py_ssize_t attr, const Py_UCS4 *name,
                               Py_ssize_t name_len);
int xp_eval_pattern_at(const xp_program *prog, struct th_tree *tree, struct th_node *context, xp_extension_fn extension,
                       void *extension_ctx, xp_name_test_fn name_test, void *name_test_ctx, xp_result *out,
                       const char **feature);

/* Matches one item at a time against a pattern compiled as an absolute location path, as XSLT counts xsl:number
   nodes: the item matches when the path selects it from root. Instead of selecting every node the path reaches, the
   test walks back from the item through its parents, so it costs the item's ancestry and siblings rather than the
   document. xp_pattern_walks_back says whether the program has a shape the walk supports: child steps, a last
   attribute step, and descendant and descendant-or-self steps without a predicate that reads the position. A walk
   keeps what it learns about the nodes it passes, so its answers hold only while the extension state the predicates
   read (the XSLT current node) stays the same. xp_pattern_walk_start resolves the name tests, through name_test when
   one is given and as xp_eval_at does otherwise, and returns 0 with *out set or a negative status as xp_eval does;
   xp_pattern_match returns 1 or 0, or such a status. */
typedef struct xp_pattern_walk xp_pattern_walk;
int xp_pattern_walks_back(const xp_program *prog);
int xp_pattern_walk_start(const xp_program *prog, struct th_tree *tree, struct th_node *root, xp_name_test_fn name_test,
                          void *name_test_ctx, xp_pattern_walk **out, const char **feature);
int xp_pattern_match(xp_pattern_walk *walk, xp_item item, xp_extension_fn extension, void *extension_ctx,
                     const char **feature);
void xp_pattern_walk_free(xp_pattern_walk *walk);

#endif /* TURBOHTML_XPATH_H */
