/* Internal header shared by the turbohtml._html translation units.

   The module is split per feature for readability (escape.c, unescape.c) but
   compiled into a single _html extension. Each feature file implements one
   public entry point declared here; _htmlmodule.c wires them into the module. */

#ifndef TURBOHTML_H
#define TURBOHTML_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <stdint.h>

/* Implemented in escape.c. Signature matches METH_VARARGS | METH_KEYWORDS. */
PyObject *turbohtml_escape(PyObject *module, PyObject *args, PyObject *kwds);

/* Implemented in unescape.c. Signature matches METH_O. */
PyObject *turbohtml_unescape(PyObject *module, PyObject *arg);

/* Implemented in markup.c, the markupsafe-compatible escape surface. escape,
   escape_silent, soft_str, and _register_markup all match METH_O. */
PyObject *turbohtml_markup_escape(PyObject *module, PyObject *s);
PyObject *turbohtml_markup_escape_silent(PyObject *module, PyObject *s);
PyObject *turbohtml_markup_soft_str(PyObject *module, PyObject *s);
PyObject *turbohtml_register_markup(PyObject *module, PyObject *type);

/* Implemented in tree_type.c. _register_xpath_string stores the str subclass that
   smart_strings xpath() results carry; signature matches METH_O. */
PyObject *turbohtml_register_xpath_string(PyObject *module, PyObject *type);

/* Implemented in linkify.c. _linkify_scan finds URL/email spans in a text run;
   _linkify_find adds the detector's custom TLD and scheme-less scheme config.
   Both signatures match METH_VARARGS. */
PyObject *turbohtml_linkify_scan(PyObject *module, PyObject *args);
PyObject *turbohtml_linkify_find(PyObject *module, PyObject *args);

/* Implemented in sanitize.c. _sanitize filters a parsed fragment in place against
   a policy; signature matches METH_VARARGS. turbohtml_node_borrow is implemented
   in tree_type.c and lends sanitize.c the tree+node a Python element wraps. */
struct th_tree;
struct th_node;
int turbohtml_node_borrow(PyObject *module, PyObject *obj, struct th_tree **tree, struct th_node **node);
PyObject *turbohtml_sanitize(PyObject *module, PyObject *args);

/* Implemented in links.c, the engine behind Node.links()/rewrite_links()/
   resolve_links(). Each takes the wrapping node (owner, for the per-tree handle
   and the Element wrappers) and the already-derived tree+root the thin C methods
   in tree_type.c hand over: turbohtml_node_links enumerates every link-bearing
   location as Link records, turbohtml_node_rewrite_links replaces each via a
   callback, turbohtml_node_resolve_links absolutizes them against a base URL with
   urllib.parse.urljoin. _register_links stores the Link record type (METH_O).
   turbohtml_node_handle and turbohtml_node_wrap_in (tree_type.c) lend links.c the
   per-tree handle for the critical section and the node->Element wrapper. */
PyObject *turbohtml_node_handle(PyObject *obj);
PyObject *turbohtml_node_wrap_in(PyObject *owner, struct th_node *node);
PyObject *turbohtml_register_links(PyObject *module, PyObject *type);
PyObject *turbohtml_node_links(PyObject *owner, struct th_tree *tree, struct th_node *root);
PyObject *turbohtml_node_rewrite_links(PyObject *owner, struct th_tree *tree, struct th_node *root, PyObject *replace);
PyObject *turbohtml_node_resolve_links(PyObject *owner, struct th_tree *tree, struct th_node *root, PyObject *base_url);

/* Implemented in tokenizer_type.c. tokenize() matches METH_VARARGS | METH_KEYWORDS;
   the internal conformance hook _tokenize_states matches METH_VARARGS. */
PyObject *turbohtml_tokenize(PyObject *module, PyObject *args, PyObject *kwargs);
PyObject *turbohtml_tokenize_states(PyObject *module, PyObject *args);

/* Implemented in treebuilder_py.c. The internal conformance hooks _parse_tree
   and _parse_fragment return the html5lib "#document" serialization of a parsed
   document / innerHTML fragment. */
PyObject *turbohtml_parse_tree(PyObject *module, PyObject *arg);
PyObject *turbohtml_parse_fragment(PyObject *module, PyObject *args);
PyObject *turbohtml_parse_only(PyObject *module, PyObject *arg);

/* Implemented in xpath.c. _xpath_parse compiles an XPath expression and returns a
   canonical S-expression of the parsed AST; the conformance hook the parser tests
   diff against. Signature matches METH_O. */
PyObject *turbohtml_xpath_parse(PyObject *module, PyObject *arg);

/* SWAR lane probes over a 64-bit word holding four UCS-2 / two UCS-4 code
   points. The has-zero test is exact as an existence test: the subtraction can
   only borrow across a lane when that lane itself is zero, so the mask is
   nonzero iff some lane equals the searched value. Lane positions inside the
   mask depend on byte order, so callers treat a nonzero mask as "somewhere in
   these lanes" and resolve the exact index with a scalar scan. */

#define UCS2_LANES 4
#define UCS4_LANES 2

static inline uint64_t swar_haslane16(uint64_t word, uint16_t value) {
    uint64_t lanes = word ^ (0x0001000100010001ULL * value);
    return (lanes - 0x0001000100010001ULL) & ~lanes & 0x8000800080008000ULL;
}

static inline uint64_t swar_haslane32(uint64_t word, uint32_t value) {
    uint64_t lanes = word ^ (0x0000000100000001ULL * value);
    return (lanes - 0x0000000100000001ULL) & ~lanes & 0x8000000080000000ULL;
}

#endif /* TURBOHTML_H */
