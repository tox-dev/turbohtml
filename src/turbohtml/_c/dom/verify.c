/* The _tree_verify binding the fuzz operation programs call after every operation (issue #1017). The tree walk lives
   in dom/mutate.c beside the NodeIterator registry it reads; this side adds the checks only the binding layer can
   see: the wrapper slot each node points at, and the per-handle caches stamped with a tree version. */

#include "dom/nodes.h"
#include "dom/verify.h"

/* A wrapper the binding table hands out for node must point back at node and at this handle, or a later lookup
   would return a wrapper for another node. */
static Py_ssize_t verify_binding(void *context, th_node *node) {
    HandleObject *handle = context;
    NodeObject *wrapper = node_binding(handle, node);
    return wrapper == NULL ? 0 : (wrapper->node != node) + (wrapper->handle != (PyObject *)handle);
}

TH_NODE_API(, PyObject *, turbohtml_tree_verify, (PyObject * module, PyObject *owner), (module, owner),
            (PyObject * module, PyObject *owner),
            is_node(owner, PyModule_GetState(module)) ? (NodeObject *)owner : NULL, NULL) {
    th_tree *tree;
    th_node *start;
    if (turbohtml_node_borrow(module, owner, &tree, &start) < 0) {
        return NULL;
    }
    HandleObject *handle = (HandleObject *)turbohtml_node_handle(owner);
    th_tree_violations found;
    Py_BEGIN_CRITICAL_SECTION(handle);
    th_tree_verify(tree, start, verify_binding, handle, &found);
    /* a cache entry compiled against a newer attribute generation, or an id map built past the tree's id version,
       means a mutation path skipped its version bump */
    uint32_t generation = th_tree_attr_generation(tree);
    /* every write that bumps the id version bumps the attribute version with it (node_attr_begin, th_node_attr_del),
       and handle_drop_index bumps the id version alone, once per mutation_version */
    found.versions += th_tree_id_version(tree) > th_tree_attr_version(tree) + handle->mutation_version;
    for (int index = 0; index < handle->sel_cache_len; index++) {
        found.versions += handle->sel_cache[index].attr_gen > generation;
    }
    if (handle->path_ids != NULL) {
        found.versions += handle->path_ids->id_version > th_tree_id_version(tree);
    }
    Py_END_CRITICAL_SECTION();
    return Py_BuildValue("(nnnn)", found.links, found.identities, found.iterators, found.versions);
}
