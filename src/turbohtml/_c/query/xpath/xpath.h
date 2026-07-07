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

typedef struct xp_program xp_program;

/* Compile an XPath expression given as code points. Returns the program, or NULL
   on a syntax error with a NUL-terminated message written into errbuf (errlen is
   its capacity). The source is not retained; names and literals are copied. */
xp_program *xp_compile(const Py_UCS4 *src, Py_ssize_t len, char *errbuf, size_t errlen);

void xp_free(xp_program *prog);

/* Render the compiled AST as a canonical S-expression (UCS4 code points), the form
   the parser tests assert against. PyMem-allocated; *out_len receives the length.
   NULL on allocation failure. */
Py_UCS4 *xp_dump(const xp_program *prog, Py_ssize_t *out_len);

/* A member of an evaluated node-set: a tree node, or one of its attributes when
   attr >= 0 (the index into node->attrs). attr == -1 means the node itself. */
struct th_node;
typedef struct {
    struct th_node *node;
    Py_ssize_t attr;
} xp_item;

typedef struct {
    xp_item *items;
    Py_ssize_t len;
    Py_ssize_t cap;
} xp_nodeset;

void xp_nodeset_free(xp_nodeset *ns);

/* Append a member to a node-set, growing it as needed; -1 on allocation failure.
   attr == -1 is the node itself, attr >= 0 one of its attributes. The marshaling
   boundary uses this to build a node-set variable from caller-supplied elements. */
int ns_push(xp_nodeset *ns, struct th_node *node, Py_ssize_t attr);

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

/* A user extension-function call: resolve `name` against the registered
   extensions and run it over `args` with `context_node` as the XPath context.
   Returns 0 with *out filled, -2 when no extension has that name (the engine then
   reports an unknown function), -1 on a Python error (the exception is set). */
struct th_node;
typedef int (*xp_extension_fn)(void *ctx, struct th_node *context_node, const Py_UCS4 *name, Py_ssize_t name_len,
                               const xp_result *args, int argc, xp_result *out);

/* Evaluate a compiled program against a context node. vars supplies $name bindings
   (NULL when none); namespaces supplies prefix-to-URI bindings for prefixed name tests
   (NULL when none); extension/extension_ctx supply user functions (extension NULL when
   none). Returns 0 with *out filled (the caller frees it with xp_result_free). Returns
   -3 for an evaluation error that maps to ValueError (a reference to an unbound variable,
   a name test whose prefix is not bound, or an expression nested past XP_MAX_DEPTH), or
   -4 for a type error that maps to TypeError (a value used where a node-set is required),
   with *feature set to a short name for the message. Returns -1 on allocation failure or
   when a Python error is already set (a malformed regex, an extension failure, or an
   unknown-function/wrong-arity call reported with the function name). */
struct th_tree;
int xp_eval(const xp_program *prog, struct th_tree *tree, struct th_node *context, const xp_bindings *vars,
            const xp_namespaces *namespaces, xp_extension_fn extension, void *extension_ctx, xp_result *out,
            const char **feature);

/* Like xp_eval, but seeds the context position and size instead of the default 1/1.
   The XSLT engine reuses this so position()/last() at the top of a select or test
   expression report the node's place in the current node list (xslt.c). */
int xp_eval_at(const xp_program *prog, struct th_tree *tree, struct th_node *context, Py_ssize_t pos, Py_ssize_t size,
               const xp_bindings *vars, const xp_namespaces *namespaces, xp_extension_fn extension, void *extension_ctx,
               xp_result *out, const char **feature);

#endif /* TURBOHTML_XPATH_H */
