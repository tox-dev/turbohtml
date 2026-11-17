/* The DOM Range and StaticRange APIs (issue #552): boundary-point math, comparison, and the
   extract/clone/delete/insert/surround content operations, all in C under the per-tree critical
   section. A boundary point is a (container node, offset) pair; a Range keeps two of them and
   maintains start <= end, while a StaticRange stores four values verbatim and is immutable.

   Offsets index code points in character data (Text/Comment/CData) and children elsewhere, so a
   Python str's own indexing lines up with a Text-node offset. A live Range moves its boundaries on
   every edit of its tree that the DOM live-range rules cover: character-data edits, splitText,
   normalize, and child removal or insertion through the tree-aware mutation API. */

#include "dom/nodes.h"
#include "core/fuzzing.h"

struct th_live_range {
    PyObject_HEAD PyObject *start_handle; /* _TreeHandle owning start_node (keeps its tree alive) */
    PyObject *end_handle;                 /* _TreeHandle owning end_node; equals start_handle for a live Range */
    th_node *start_node;
    th_node *end_node;
    Py_ssize_t start_offset;
    Py_ssize_t end_offset;
    th_tree *live_tree; /* the tree whose live_ranges list holds this Range, NULL for a StaticRange */
    struct th_live_range *live_prev;
    struct th_live_range *live_next;
};
typedef struct th_live_range RangeObject;

#define RANGE_MAX_RECURSIVE_DEPTH ((Py_ssize_t)TH_DEPTH_LIMIT(400))

/* Character data whose offset indexes code points and which the content operations split in place. */
static int is_char_data(const th_node *node) {
    switch (node->type) {
    case TH_NODE_TEXT:
    case TH_NODE_COMMENT:
    case TH_NODE_CDATA:
        return 1;
    default:
        return 0;
    }
}

/* Child offsets are positions in a linked list, so a Range call that maps a child to its offset or back walks the
   siblings before it, and a caller's loop over a long child list would pay a walk per call. The handle keeps a few
   (parent, child, index) cursors instead: a lookup steps from the nearest cursor of the same parent and moves that
   cursor along. Edits through other tree APIs bump mutation_version and normalize unlinks without one, so either
   empties the cursors; the edits Range makes itself go through range_insert_before and range_remove, which keep them
   in step. */
#define SIBLING_CURSORS (sizeof(((HandleObject *)NULL)->sibling_cursors) / sizeof(struct sibling_cursor))

static int cursors_current(const HandleObject *handle) {
    return handle->sibling_cursor_version == handle->mutation_version &&
           handle->sibling_cursor_unlinks == th_tree_unlinks();
}

static void cursors_refresh(HandleObject *handle) {
    if (!cursors_current(handle)) {
        memset(handle->sibling_cursors, 0, sizeof(handle->sibling_cursors));
        handle->sibling_cursor_version = handle->mutation_version;
        handle->sibling_cursor_unlinks = th_tree_unlinks();
    }
}

/* Move slot, or the next cursor in turn when slot is NULL, onto child at index. */
static void cursor_store(HandleObject *handle, struct sibling_cursor *slot, th_node *parent, th_node *child,
                         Py_ssize_t index) {
    if (slot == NULL) {
        slot = &handle->sibling_cursors[handle->sibling_cursor_next++ % SIBLING_CURSORS];
    }
    slot->parent = parent;
    slot->child = child;
    slot->index = index;
}

/* The index of node, which has a parent, among its siblings. The walk goes back toward the first child and, in step,
   both ways toward the children the cursors hold for that parent, and ends at whichever it meets first. A first child
   needs no cursor, which keeps the common short child lists off the cursor bookkeeping. */
static Py_ssize_t sibling_index(HandleObject *handle, th_node *node) {
    if (node->prev_sibling == NULL) {
        return 0;
    }
    cursors_refresh(handle);
    struct sibling_cursor *known[SIBLING_CURSORS];
    size_t known_count = 0;
    for (size_t slot = 0; slot < SIBLING_CURSORS; slot++) {
        if (handle->sibling_cursors[slot].parent == node->parent) {
            known[known_count++] = &handle->sibling_cursors[slot];
        }
    }
    th_node *before = node;
    th_node *after = known_count > 0 ? node : NULL;
    for (Py_ssize_t steps = 0;; steps++) {
        for (size_t slot = 0; slot < known_count; slot++) {
            Py_ssize_t index = known[slot]->child == before  ? known[slot]->index + steps
                               : known[slot]->child == after ? known[slot]->index - steps
                                                             : -1;
            if (index >= 0) {
                if (steps > 0) {
                    cursor_store(handle, known[slot], node->parent, node, index);
                }
                return index;
            }
        }
        TH_WORK_STEP();
        before = before->prev_sibling;
        if (before == NULL) {
            cursor_store(handle, NULL, node->parent, node, steps);
            return steps;
        }
        if (after != NULL) {
            after = after->next_sibling;
        }
    }
}

/* The child of parent at index, or NULL past the last child, walked from the first child or from the nearest child
   the cursors hold for parent. */
static th_node *child_at(HandleObject *handle, th_node *parent, Py_ssize_t index) {
    if (index == 0) {
        return parent->first_child;
    }
    cursors_refresh(handle);
    struct sibling_cursor *nearest = NULL;
    Py_ssize_t distance = index;
    for (size_t slot = 0; slot < SIBLING_CURSORS; slot++) {
        struct sibling_cursor *cursor = &handle->sibling_cursors[slot];
        Py_ssize_t gap = cursor->index > index ? cursor->index - index : index - cursor->index;
        if (cursor->parent == parent && gap < distance) {
            nearest = cursor;
            distance = gap;
        }
    }
    th_node *child = nearest == NULL ? parent->first_child : nearest->child;
    Py_ssize_t at = nearest == NULL ? 0 : nearest->index;
    for (; child != NULL && at < index; at++) {
        TH_WORK_STEP();
        child = child->next_sibling;
    }
    for (; at > index; at--) {
        TH_WORK_STEP();
        child = child->prev_sibling;
    }
    if (child != NULL && (nearest == NULL || distance > 0)) {
        cursor_store(handle, nearest, parent, child, index);
    }
    return child;
}

static Py_ssize_t child_count(HandleObject *handle, th_node *parent) {
    return parent->last_child == NULL ? 0 : sibling_index(handle, parent->last_child) + 1;
}

/* The DOM "length of a node": the code-point count for character data, else the child count. */
static Py_ssize_t node_length(HandleObject *handle, th_node *node) {
    return is_char_data(node) ? node->text_len : child_count(handle, node);
}

/* Link child into parent before ref (NULL appends), moving the cursors of parent past it. A cursor whose child is not
   next to the new one cannot tell which side it is on and is dropped. Stale cursors move too, which is harmless: the
   next lookup empties them. */
static void range_insert_before(HandleObject *handle, th_node *parent, th_node *child, th_node *ref) {
    th_node_insert_before_live(handle->tree, parent, child, ref);
    for (size_t slot = 0; slot < SIBLING_CURSORS; slot++) {
        struct sibling_cursor *cursor = &handle->sibling_cursors[slot];
        if (cursor->parent != parent) {
            continue;
        }
        if (child->next_sibling == cursor->child || child->prev_sibling == NULL) {
            cursor->index++;
        } else if (child->prev_sibling != cursor->child && child->next_sibling != NULL) {
            cursor->parent = NULL;
        }
    }
}

/* Unlink child from its parent, if any, moving the cursors of that parent as range_insert_before does. */
static void range_remove(HandleObject *handle, th_node *child) {
    th_node *parent = child->parent;
    int current = parent != NULL && cursors_current(handle);
    for (size_t slot = 0; current && slot < SIBLING_CURSORS; slot++) {
        struct sibling_cursor *cursor = &handle->sibling_cursors[slot];
        if (cursor->parent != parent) {
            continue;
        }
        if (cursor->child == child) {
            if (child->prev_sibling != NULL) {
                cursor->child = child->prev_sibling;
                cursor->index--;
            } else {
                cursor->child = child->next_sibling;
                cursor->parent = child->next_sibling == NULL ? NULL : parent;
            }
        } else if (child->next_sibling == cursor->child || child->prev_sibling == NULL) {
            cursor->index--;
        } else if (child->prev_sibling != cursor->child && child->next_sibling != NULL) {
            cursor->parent = NULL;
        }
    }
    th_node_remove_silent(handle->tree, child);
    if (current) {
        handle->sibling_cursor_unlinks = th_tree_unlinks();
    }
}

/* handle_drop_index for an edit Range makes itself, which keeps the cursors current: range_insert_before and
   range_remove move them with every change of a child list. */
static void range_drop_index(HandleObject *handle) {
    int current = cursors_current(handle);
    handle_drop_index((PyObject *)handle);
    if (current) {
        handle->sibling_cursor_version = handle->mutation_version;
    }
}

/* import_foreign_node into the range's tree. The import links nothing into this tree and bumps the version once, so
   the cursors stay current unless the version moved further: another thread edited this tree while the import gave
   the lock up. */
static int range_import(HandleObject *handle, PyObject **node_ref) {
    int current = cursors_current(handle);
    uint64_t version = handle->mutation_version;
    int imported = import_foreign_node((PyObject *)handle, node_ref);
    if (current && handle->mutation_version == version + 1) {
        handle->sibling_cursor_version = handle->mutation_version;
    }
    return imported;
}

/* The tree order of two different nodes of one tree: -1 when left comes first, else 1. */
static int node_order(HandleObject *handle, th_node *left, th_node *right) {
    Py_ssize_t left_depth = 0;
    for (th_node *node = left; node->parent != NULL; node = node->parent) {
        left_depth++;
    }
    Py_ssize_t right_depth = 0;
    for (th_node *node = right; node->parent != NULL; node = node->parent) {
        right_depth++;
    }
    int order = node_climb_to_siblings(&left, left_depth, &right, right_depth);
    if (order != 0) {
        return order;
    }
    return sibling_index(handle, left) < sibling_index(handle, right) ? -1 : 1;
}

/* bp_compare for two different nodes with left_node not after right_node in tree order: -1, or +1 when left_node is
   an ancestor of right_node and the point sits after the child that leads to it. */
static int bp_compare_ordered(HandleObject *handle, th_node *left_node, Py_ssize_t left_offset, th_node *right_node) {
    if (is_ancestor(left_node, right_node)) {
        th_node *child = right_node;
        while (child->parent != left_node) {
            child = child->parent;
        }
        if (sibling_index(handle, child) < left_offset) {
            return 1;
        }
    }
    return -1;
}

/* Compare two boundary points sharing a root, the tree of handle: -1 (left before right), 0 (equal), +1 (left after).
   The swap is done once and not by recursion: two nodes that share no root each sort after the other, which used to
   recurse without end. */
static int bp_compare(HandleObject *handle, th_node *left_node, Py_ssize_t left_offset, th_node *right_node,
                      Py_ssize_t right_offset) {
    if (left_node == right_node) {
        if (left_offset == right_offset) {
            return 0;
        }
        return left_offset < right_offset ? -1 : 1;
    }
    if (node_order(handle, left_node, right_node) > 0) {
        return bp_compare_ordered(handle, right_node, right_offset, left_node) == -1 ? 1 : -1;
    }
    return bp_compare_ordered(handle, left_node, left_offset, right_node);
}

/* Whether two boundary containers share a root; a differing handle guarantees a different tree. */
static int same_root(PyObject *left_handle, th_node *left_node, PyObject *right_handle, th_node *right_node) {
    return left_handle == right_handle && node_root(left_node) == node_root(right_node);
}

/* Realize a character-data node's text into *text (NULL for parsed empty data). Returns 0, or -1 with MemoryError set
   when copying a zero-copy text span fails. */
static int realize_char_data(th_tree *tree, th_node *node, const Py_UCS4 **text) {
    *text = th_node_realize_text(tree, node);
    if (*text == NULL && node->text_len > 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
        PyErr_NoMemory();                      /* GCOVR_EXCL_LINE: allocation failure */
        return -1;                             /* GCOVR_EXCL_LINE: allocation failure */
    }
    return 0;
}

static inline const Py_UCS4 *char_data_offset(const Py_UCS4 *text, Py_ssize_t offset) {
    return text == NULL ? NULL : text + offset; /* GCOVR_EXCL_BR_LINE: empty text or allocation failure */
}

/* Split a character-data node at offset: the tail becomes a new sibling right after it, which is
   returned. The tail reads the rest of the head's buffer, so a split stores no text. NULL on
   allocation failure. */
static th_node *split_data_node(HandleObject *handle, th_node *node, Py_ssize_t offset) {
    th_tree *tree = handle->tree;
    const Py_UCS4 *text;
    if (realize_char_data(tree, node, &text) < 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
        return NULL;                                /* GCOVR_EXCL_LINE: allocation failure */
    }
    th_node *tail = th_tree_make_data_node(tree, node->type, NULL, 0);
    if (tail == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (offset < node->text_len) {
        tail->text = node->text + offset;
        tail->text_len = node->text_len - offset;
        tail->text_exclusive = node->text_exclusive;
        node->text_capacity_shift = 0; /* the tail reads the rest of the buffer, so the head must not grow into it */
    }
    range_insert_before(handle, node->parent, tail, node->next_sibling);
    th_live_split(tree, node, offset, tail);
    if (th_node_delete_data(tree, node, offset, node->text_len) < 0) { /* GCOVR_EXCL_BR_LINE: OOM only */
        return NULL;                                                   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return tail;
}

/* Move every child of a throwaway fragment onto parent, in order. */
static void adopt_fragment_children(HandleObject *handle, th_node *parent, th_node *fragment) {
    while (fragment->first_child != NULL) {
        th_node *child = fragment->first_child;
        range_remove(handle, child);
        range_insert_before(handle, parent, child, NULL);
    }
}

/* The children of common that the range fully contains, checked for a contained DocumentType (a
   HierarchyRequestError). first and last are the children of common on the boundary paths, NULL for a boundary in
   common itself. *out_count is the run length; the returned array is PyMem-owned (NULL when empty). Sets an exception
   and returns -1 through *error on a doctype or allocation failure. */
static th_node **collect_contained(HandleObject *handle, th_node *common, th_node *first, Py_ssize_t start_offset,
                                   th_node *last, Py_ssize_t end_offset, Py_ssize_t *out_count, int *error) {
    *error = 0;
    th_node *run = first == NULL ? child_at(handle, common, start_offset) : first->next_sibling;
    Py_ssize_t limit = PY_SSIZE_T_MAX;
    if (last == NULL) {
        limit = end_offset - (first == NULL ? start_offset : sibling_index(handle, first) + 1);
    }
    Py_ssize_t count = 0;
    for (th_node *child = run; child != last && count < limit; child = child->next_sibling) {
        TH_WORK_STEP();
        count++;
    }
    *out_count = count;
    if (count == 0) {
        return NULL;
    }
    th_node **nodes = PyMem_Malloc((size_t)count * sizeof(th_node *));
    if (nodes == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        *error = 1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node *child = run;
    for (Py_ssize_t index = 0; index < count; index++, child = child->next_sibling) {
        if (child->type == TH_NODE_DOCTYPE) {
            PyErr_SetString(PyExc_ValueError, "cannot extract a range spanning a doctype");
            PyMem_Free(nodes);
            *error = 1;
            return NULL;
        }
        nodes[index] = child;
    }
    return nodes;
}

/* The common ancestor of the two boundary containers (they share a root). The two paths are brought level and then
   climbed together; testing each ancestor of one container against the other would walk to the root at every step. */
static th_node *common_ancestor(th_node *start_node, th_node *end_node) {
    if (start_node == end_node) {
        return start_node;
    }
    Py_ssize_t start_depth = 0;
    for (th_node *node = start_node; node->parent != NULL; node = node->parent) {
        TH_WORK_STEP();
        start_depth++;
    }
    Py_ssize_t end_depth = 0;
    for (th_node *node = end_node; node->parent != NULL; node = node->parent) {
        TH_WORK_STEP();
        end_depth++;
    }
    th_node *start_at = start_node;
    th_node *end_at = end_node;
    for (; start_depth > end_depth; start_depth--) {
        TH_WORK_STEP();
        start_at = start_at->parent;
    }
    for (; end_depth > start_depth; end_depth--) {
        TH_WORK_STEP();
        end_at = end_at->parent;
    }
    while (start_at != end_at) {
        TH_WORK_STEP();
        start_at = start_at->parent;
        end_at = end_at->parent;
    }
    return start_at;
}

/* clone/extract follow the two partially contained boundary paths recursively. Reject an unsafe path before allocating
   a fragment or mutating the source; contained subtrees use the iterative DOM copy primitive. */
static int range_check_recursive_depth(RangeObject *range, const char *operation) {
    if (range->start_node == range->end_node) {
        return 0;
    }
    th_node *common = common_ancestor(range->start_node, range->end_node);
    Py_ssize_t start_depth = 0;
    for (th_node *node = range->start_node; node != common; node = node->parent) {
        start_depth++;
    }
    Py_ssize_t end_depth = 0;
    for (th_node *node = range->end_node; node != common; node = node->parent) {
        end_depth++;
    }
    if (start_depth < RANGE_MAX_RECURSIVE_DEPTH && end_depth < RANGE_MAX_RECURSIVE_DEPTH) {
        return 0;
    }
    PyErr_Format(PyExc_RecursionError, "%s does not support boundary paths %zd levels or deeper", operation,
                 RANGE_MAX_RECURSIVE_DEPTH);
    return -1;
}

/* The child of common on the path down to boundary, a strict descendant of common. The start's is the first child the
   range partially contains and the end's the last, so the climb finds either without testing every sibling against
   both boundary paths. */
static th_node *partial_child(th_node *common, th_node *boundary) {
    th_node *child = boundary;
    while (child->parent != common) {
        TH_WORK_STEP();
        child = child->parent;
    }
    return child;
}

/* Extract the range's content into a fresh fragment, moving contained nodes and splitting the
   boundary character data out of the source tree. Recursive over straddled elements: each is the
   common ancestor of the boundaries it recurses with, so common is handed down rather than found
   again from the root at every level. NULL on error with an exception set; the returned CONTENT
   node lives in tree. */
static th_node *do_extract(HandleObject *handle, th_node *common, th_node *start_node, Py_ssize_t start_offset,
                           th_node *end_node, Py_ssize_t end_offset) {
    th_tree *tree = handle->tree;
    th_node *fragment = th_tree_make_fragment(tree);
    if (fragment == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory();   /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (start_node == end_node && start_offset == end_offset) {
        return fragment;
    }
    if (start_node == end_node && is_char_data(start_node)) {
        const Py_UCS4 *text;
        if (realize_char_data(tree, start_node, &text) < 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
            return NULL;                                      /* GCOVR_EXCL_LINE: allocation failure */
        }
        th_node *piece = th_tree_make_data_node(tree, start_node->type, char_data_offset(text, start_offset),
                                                end_offset - start_offset);
        if (piece == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(fragment, piece);
        if (th_node_delete_data(tree, start_node, start_offset, end_offset) < 0) { /* GCOVR_EXCL_BR_LINE: OOM only */
            return NULL;                                                           /* GCOVR_EXCL_LINE: OOM path */
        }
        return fragment;
    }
    th_node *first = start_node == common ? NULL : partial_child(common, start_node);
    th_node *last = end_node == common ? NULL : partial_child(common, end_node);
    Py_ssize_t contained_count;
    int error;
    th_node **contained =
        collect_contained(handle, common, first, start_offset, last, end_offset, &contained_count, &error);
    if (error) {
        return NULL;
    }
    if (first != NULL && is_char_data(first)) {
        const Py_UCS4 *text;
        if (realize_char_data(tree, start_node, &text) < 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
            PyMem_Free(contained);                            /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;                                      /* GCOVR_EXCL_LINE: allocation failure */
        }
        th_node *piece = th_tree_make_data_node(tree, start_node->type, text == NULL ? NULL : text + start_offset,
                                                start_node->text_len - start_offset);
        if (piece == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyMem_Free(contained); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(fragment, piece);
        /* th_node_delete_data only fails on OOM */
        if (th_node_delete_data(tree, start_node, start_offset, start_node->text_len) < 0) { /* GCOVR_EXCL_BR_LINE */
            PyMem_Free(contained);                                                           /* GCOVR_EXCL_LINE: OOM */
            return NULL;                                                                     /* GCOVR_EXCL_LINE: OOM */
        }
    } else if (first != NULL) {
        th_node *clone = th_tree_copy_node_shallow(tree, tree, first);
        if (clone == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyMem_Free(contained); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(fragment, clone);
        th_node *sub = do_extract(handle, first, start_node, start_offset, first, child_count(handle, first));
        if (sub == NULL) {         /* GCOVR_EXCL_BR_LINE: only the OOM paths of the recursion reach here */
            PyMem_Free(contained); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        adopt_fragment_children(handle, clone, sub);
    }
    for (Py_ssize_t index = 0; index < contained_count; index++) {
        range_remove(handle, contained[index]);
        th_node_append_child(fragment, contained[index]);
    }
    PyMem_Free(contained);
    if (last != NULL && is_char_data(last)) {
        const Py_UCS4 *text;
        if (realize_char_data(tree, end_node, &text) < 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
            return NULL;                                    /* GCOVR_EXCL_LINE: allocation failure */
        }
        th_node *piece = th_tree_make_data_node(tree, end_node->type, text, end_offset);
        if (piece == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(fragment, piece);
        if (th_node_delete_data(tree, end_node, 0, end_offset) < 0) { /* GCOVR_EXCL_BR_LINE: OOM only */
            return NULL;                                              /* GCOVR_EXCL_LINE: OOM path */
        }
    } else if (last != NULL) {
        th_node *clone = th_tree_copy_node_shallow(tree, tree, last);
        if (clone == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(fragment, clone);
        th_node *sub = do_extract(handle, last, last, 0, end_node, end_offset);
        if (sub == NULL) { /* GCOVR_EXCL_BR_LINE: only the OOM paths of the recursion reach here */
            return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        adopt_fragment_children(handle, clone, sub);
    }
    return fragment;
}

/* Clone the range's content into a fresh fragment, leaving the source tree untouched. Recursive
   over straddled elements, handing common down as do_extract does. NULL on error with an exception
   set. */
static th_node *do_clone(HandleObject *handle, th_node *common, th_node *start_node, Py_ssize_t start_offset,
                         th_node *end_node, Py_ssize_t end_offset) {
    th_tree *tree = handle->tree;
    th_node *fragment = th_tree_make_fragment(tree);
    if (fragment == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory();   /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (start_node == end_node && start_offset == end_offset) {
        return fragment;
    }
    if (start_node == end_node && is_char_data(start_node)) {
        const Py_UCS4 *text;
        if (realize_char_data(tree, start_node, &text) < 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
            return NULL;                                      /* GCOVR_EXCL_LINE: allocation failure */
        }
        th_node *piece = th_tree_make_data_node(tree, start_node->type, char_data_offset(text, start_offset),
                                                end_offset - start_offset);
        if (piece == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(fragment, piece);
        return fragment;
    }
    th_node *first = start_node == common ? NULL : partial_child(common, start_node);
    th_node *last = end_node == common ? NULL : partial_child(common, end_node);
    Py_ssize_t contained_count;
    int error;
    th_node **contained =
        collect_contained(handle, common, first, start_offset, last, end_offset, &contained_count, &error);
    if (error) {
        return NULL;
    }
    if (first != NULL && is_char_data(first)) {
        const Py_UCS4 *text;
        if (realize_char_data(tree, start_node, &text) < 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
            PyMem_Free(contained);                            /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;                                      /* GCOVR_EXCL_LINE: allocation failure */
        }
        th_node *piece = th_tree_make_data_node(tree, start_node->type, char_data_offset(text, start_offset),
                                                start_node->text_len - start_offset);
        if (piece == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyMem_Free(contained); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(fragment, piece);
    } else if (first != NULL) {
        th_node *clone = th_tree_copy_node_shallow(tree, tree, first);
        if (clone == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyMem_Free(contained); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(fragment, clone);
        th_node *sub = do_clone(handle, first, start_node, start_offset, first, child_count(handle, first));
        if (sub == NULL) {         /* GCOVR_EXCL_BR_LINE: only the OOM paths of the recursion reach here */
            PyMem_Free(contained); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        adopt_fragment_children(handle, clone, sub);
    }
    for (Py_ssize_t index = 0; index < contained_count; index++) {
        th_node *copy = th_tree_copy_node(tree, tree, contained[index]);
        if (copy == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyMem_Free(contained); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(fragment, copy);
    }
    PyMem_Free(contained);
    if (last != NULL && is_char_data(last)) {
        const Py_UCS4 *text;
        if (realize_char_data(tree, end_node, &text) < 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
            return NULL;                                    /* GCOVR_EXCL_LINE: allocation failure */
        }
        th_node *piece = th_tree_make_data_node(tree, end_node->type, text, end_offset);
        if (piece == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(fragment, piece);
    } else if (last != NULL) {
        th_node *clone = th_tree_copy_node_shallow(tree, tree, last);
        if (clone == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(fragment, clone);
        th_node *sub = do_clone(handle, last, last, 0, end_node, end_offset);
        if (sub == NULL) { /* GCOVR_EXCL_BR_LINE: only the OOM paths of the recursion reach here */
            return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        adopt_fragment_children(handle, clone, sub);
    }
    return fragment;
}

/* The collapsed boundary a range takes after extract/delete, computed before the tree mutates. */
static void collapse_point(HandleObject *handle, th_node *common, th_node *start_node, Py_ssize_t start_offset,
                           th_node **out_node, Py_ssize_t *out_offset) {
    if (start_node == common) {
        *out_node = start_node;
        *out_offset = start_offset;
        return;
    }
    *out_node = common;
    *out_offset = sibling_index(handle, partial_child(common, start_node)) + 1;
}

/* --- live ranges: the DOM live-range update rules --- */

/* A Range joins its tree's live_ranges list while its boundaries sit in that tree, so every edit of the tree can move
   them. A StaticRange never joins. */
static void live_untrack(RangeObject *range) {
    th_tree *tree = range->live_tree;
    if (tree == NULL) {
        return;
    }
    struct th_live_range **head = th_tree_live_ranges_ptr(tree);
    if (range->live_prev != NULL) {
        range->live_prev->live_next = range->live_next;
    } else {
        *head = range->live_next;
    }
    if (range->live_next != NULL) {
        range->live_next->live_prev = range->live_prev;
    }
    range->live_tree = NULL;
    range->live_prev = NULL;
    range->live_next = NULL;
}

static void live_track(RangeObject *range, th_tree *tree) {
    if (range->live_tree == tree) {
        return;
    }
    live_untrack(range);
    struct th_live_range **head = th_tree_live_ranges_ptr(tree);
    range->live_tree = tree;
    range->live_next = *head;
    if (*head != NULL) {
        (*head)->live_prev = range;
    }
    *head = range;
}

static Py_ssize_t index_in_parent(const th_node *child) {
    Py_ssize_t index = 0;
    for (const th_node *sibling = child->prev_sibling; sibling != NULL; sibling = sibling->prev_sibling) {
        index++;
    }
    return index;
}

static int is_inclusive_descendant(const th_node *node, const th_node *ancestor) {
    for (const th_node *walk = node; walk != NULL; walk = walk->parent) {
        if (walk == ancestor) {
            return 1;
        }
    }
    return 0;
}

/* The replace-data rule for one boundary: a point inside the replaced run snaps to its start, a point after it shifts
   by the change in length. */
static Py_ssize_t replaced_offset(Py_ssize_t position, Py_ssize_t offset, Py_ssize_t count, Py_ssize_t data_len) {
    if (position > offset + count) {
        return position + data_len - count;
    }
    if (position > offset) {
        return offset;
    }
    return position;
}

/* The index of child is looked up only once a boundary needs it, so an edit no Range points into costs nothing. */
static void removal_point(th_node **container, Py_ssize_t *offset, th_node *child, Py_ssize_t *index) {
    if (is_inclusive_descendant(*container, child)) {
        if (*index < 0) {
            *index = index_in_parent(child);
        }
        *container = child->parent;
        *offset = *index;
    } else if (*container == child->parent) {
        if (*index < 0) {
            *index = index_in_parent(child);
        }
        if (*offset > *index) {
            *offset -= 1;
        }
    }
}

void th_live_replace_data(th_tree *tree, th_node *node, Py_ssize_t offset, Py_ssize_t count, Py_ssize_t data_len) {
    for (RangeObject *range = *th_tree_live_ranges_ptr(tree); range != NULL; range = range->live_next) {
        if (range->start_node == node) {
            range->start_offset = replaced_offset(range->start_offset, offset, count, data_len);
        }
        if (range->end_node == node) {
            range->end_offset = replaced_offset(range->end_offset, offset, count, data_len);
        }
    }
}

void th_live_removed(th_tree *tree, th_node *child) {
    if (child->parent == NULL) {
        return;
    }
    Py_ssize_t index = -1;
    for (RangeObject *range = *th_tree_live_ranges_ptr(tree); range != NULL; range = range->live_next) {
        removal_point(&range->start_node, &range->start_offset, child, &index);
        removal_point(&range->end_node, &range->end_offset, child, &index);
    }
}

/* Called once child is linked, so its index is its count of earlier siblings. */
void th_live_inserted(th_tree *tree, th_node *child) {
    th_node *parent = child->parent;
    Py_ssize_t index = -1;
    for (RangeObject *range = *th_tree_live_ranges_ptr(tree); range != NULL; range = range->live_next) {
        if (range->start_node == parent) {
            if (index < 0) {
                index = index_in_parent(child);
            }
            if (range->start_offset > index) {
                range->start_offset += 1;
            }
        }
        if (range->end_node == parent) {
            if (index < 0) {
                index = index_in_parent(child);
            }
            if (range->end_offset > index) {
                range->end_offset += 1;
            }
        }
    }
}

/* The splitText rules after tail, the new node, is linked after node. The insertion rules have already moved the
   parent's boundaries past tail's index; a boundary just after node moves to after tail, and a boundary past offset in
   node moves into tail. */
void th_live_split(th_tree *tree, th_node *node, Py_ssize_t offset, th_node *tail) {
    th_node *parent = tail->parent;
    Py_ssize_t index = index_in_parent(tail);
    for (RangeObject *range = *th_tree_live_ranges_ptr(tree); range != NULL; range = range->live_next) {
        if (range->start_node == node && range->start_offset > offset) {
            range->start_node = tail;
            range->start_offset -= offset;
        }
        if (range->end_node == node && range->end_offset > offset) {
            range->end_node = tail;
            range->end_offset -= offset;
        }
        if (range->start_node == parent && range->start_offset == index) {
            range->start_offset += 1;
        }
        if (range->end_node == parent && range->end_offset == index) {
            range->end_offset += 1;
        }
    }
}

/* normalize folds part into into, whose text is offset code points long at that point: the boundaries in part move
   to the same point in into. */
void th_live_merged(th_tree *tree, th_node *part, th_node *into, Py_ssize_t offset) {
    for (RangeObject *range = *th_tree_live_ranges_ptr(tree); range != NULL; range = range->live_next) {
        if (range->start_node == part) {
            range->start_node = into;
            range->start_offset += offset;
        }
        if (range->end_node == part) {
            range->end_node = into;
            range->end_offset += offset;
        }
    }
}

/* --- boundary helpers on the RangeObject --- */

static int validate_boundary(PyObject *handle, th_node *node, Py_ssize_t offset) {
    if (node->type == TH_NODE_DOCTYPE) {
        PyErr_SetString(PyExc_ValueError, "a boundary point cannot be inside a doctype");
        return -1;
    }
    if (offset < 0 || (is_char_data(node) ? offset > node->text_len
                                          : offset > 0 && child_at((HandleObject *)handle, node, offset - 1) == NULL)) {
        PyErr_SetString(PyExc_IndexError, "offset is out of range for the node");
        return -1;
    }
    return 0;
}

static int range_is_collapsed(RangeObject *self) {
    return self->start_node == self->end_node && self->start_offset == self->end_offset;
}

static void store_start(RangeObject *self, PyObject *handle, th_node *node, Py_ssize_t offset) {
    live_track(self, ((HandleObject *)handle)->tree);
    Py_SETREF(self->start_handle, Py_NewRef(handle));
    self->start_node = node;
    self->start_offset = offset;
}

static void store_end(RangeObject *self, PyObject *handle, th_node *node, Py_ssize_t offset) {
    Py_SETREF(self->end_handle, Py_NewRef(handle));
    self->end_node = node;
    self->end_offset = offset;
}

/* The DOM "set the start" step: drag the end onto the new point when it lands in another tree or
   past the current end, so start <= end holds. */
static void set_start_bp(RangeObject *self, PyObject *handle, th_node *node, Py_ssize_t offset) {
    if (!same_root(handle, node, self->end_handle, self->end_node) ||
        bp_compare((HandleObject *)handle, node, offset, self->end_node, self->end_offset) > 0) {
        store_end(self, handle, node, offset);
    }
    store_start(self, handle, node, offset);
}

static void set_end_bp(RangeObject *self, PyObject *handle, th_node *node, Py_ssize_t offset) {
    if (!same_root(handle, node, self->start_handle, self->start_node) ||
        bp_compare((HandleObject *)handle, node, offset, self->start_node, self->start_offset) < 0) {
        store_start(self, handle, node, offset);
    }
    store_end(self, handle, node, offset);
}

/* --- construction --- */

/* A concurrent move into another tree rebinds the container's handle and node under its owner's lock, so the guard
   holds that lock while the boundary is read and validated. */
TH_NODE_API(static, PyObject *, range_new, (PyTypeObject * type, PyObject *args, PyObject *kwds), (type, args, kwds),
            (PyTypeObject * type, PyObject *args, PyObject *kwds),
            node_argument(PyType_GetModuleState(type), args, kwds, 0, "container"), NULL) {
    static char *keywords[] = {"container", "offset", NULL};
    PyObject *container;
    Py_ssize_t offset = 0;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|n:Range", keywords, &container, &offset)) {
        return NULL;
    }
    module_state *state = PyType_GetModuleState(type);
    if (!is_node(container, state)) {
        PyErr_SetString(PyExc_TypeError, "container must be a node");
        return NULL;
    }
    th_node *node = ((NodeObject *)container)->node;
    if (validate_boundary(((NodeObject *)container)->handle, node, offset) < 0) {
        return NULL;
    }
    RangeObject *self = (RangeObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *handle = ((NodeObject *)container)->handle;
    self->start_handle = Py_NewRef(handle);
    self->end_handle = Py_NewRef(handle);
    self->start_node = node;
    self->end_node = node;
    self->start_offset = offset;
    self->end_offset = offset;
    live_track(self, ((HandleObject *)handle)->tree);
    return (PyObject *)self;
}

static void range_dealloc(PyObject *self) {
    RangeObject *range = (RangeObject *)self;
    PyTypeObject *type = Py_TYPE(self);
    if (range->live_tree != NULL) {
        Py_BEGIN_CRITICAL_SECTION(range->start_handle); /* walkers of the live-range list hold this tree's lock */
        live_untrack(range);
        Py_END_CRITICAL_SECTION();
    }
    Py_XDECREF(range->start_handle);
    Py_XDECREF(range->end_handle);
    type->tp_free(self);
    Py_DECREF(type);
}

/* --- AbstractRange getters (shared by Range and StaticRange) --- */

static PyObject *get_start_container(PyObject *self, void *Py_UNUSED(closure)) {
    RangeObject *range = (RangeObject *)self;
    return node_wrap(state_of(self), range->start_handle, range->start_node);
}

static PyObject *get_start_offset(PyObject *self, void *Py_UNUSED(closure)) {
    return PyLong_FromSsize_t(((RangeObject *)self)->start_offset);
}

static PyObject *get_end_container(PyObject *self, void *Py_UNUSED(closure)) {
    RangeObject *range = (RangeObject *)self;
    return node_wrap(state_of(self), range->end_handle, range->end_node);
}

static PyObject *get_end_offset(PyObject *self, void *Py_UNUSED(closure)) {
    return PyLong_FromSsize_t(((RangeObject *)self)->end_offset);
}

static PyObject *get_collapsed(PyObject *self, void *Py_UNUSED(closure)) {
    return PyBool_FromLong(range_is_collapsed((RangeObject *)self));
}

static PyObject *get_common_ancestor(PyObject *self, void *Py_UNUSED(closure)) {
    RangeObject *range = (RangeObject *)self;
    PyObject *result = NULL;
    /* the comparison moves the handle's sibling cursors */
    Py_BEGIN_CRITICAL_SECTION(range->start_handle);
    result = node_wrap(state_of(self), range->start_handle, common_ancestor(range->start_node, range->end_node));
    Py_END_CRITICAL_SECTION();
    return result;
}

/* --- boundary setters --- */

/* Borrow the (handle, node) a node argument wraps; sets a TypeError and returns -1 otherwise. A concurrent move into
   another tree rebinds the pair under the owner's lock, so each caller runs under the guard on its node argument. */
static int node_arg(PyObject *self, PyObject *arg, PyObject **handle, th_node **node) {
    if (!is_node(arg, state_of(self))) {
        PyErr_SetString(PyExc_TypeError, "expected a node");
        return -1;
    }
    *handle = ((NodeObject *)arg)->handle;
    *node = ((NodeObject *)arg)->node;
    return 0;
}

static PyObject *set_boundary(PyObject *self, PyObject *args, int is_start) {
    PyObject *container;
    Py_ssize_t offset;
    if (!PyArg_ParseTuple(args, "On", &container, &offset)) {
        return NULL;
    }
    PyObject *handle;
    th_node *node;
    if (node_arg(self, container, &handle, &node) < 0 || validate_boundary(handle, node, offset) < 0) {
        return NULL;
    }
    if (is_start) {
        set_start_bp((RangeObject *)self, handle, node, offset);
    } else {
        set_end_bp((RangeObject *)self, handle, node, offset);
    }
    Py_RETURN_NONE;
}

TH_NODE_API(static, PyObject *, range_set_start, (PyObject * self, PyObject *args), (self, args),
            (PyObject * self, PyObject *args), node_argument(state_of(self), args, NULL, 0, NULL), NULL) {
    return set_boundary(self, args, 1);
}

TH_NODE_API(static, PyObject *, range_set_end, (PyObject * self, PyObject *args), (self, args),
            (PyObject * self, PyObject *args), node_argument(state_of(self), args, NULL, 0, NULL), NULL) {
    return set_boundary(self, args, 0);
}

/* The parent and index of a node used as a before/after reference; sets InvalidNodeTypeError and
   returns -1 when the node has no parent. */
static int reference_parent(PyObject *self, PyObject *arg, PyObject **handle, th_node **parent, Py_ssize_t *index) {
    th_node *node;
    if (node_arg(self, arg, handle, &node) < 0) {
        return -1;
    }
    if (node->parent == NULL) {
        PyErr_SetString(PyExc_ValueError, "the node has no parent");
        return -1;
    }
    *parent = node->parent;
    *index = sibling_index((HandleObject *)*handle, node);
    return 0;
}

TH_NODE_API(static, PyObject *, range_set_start_before, (PyObject * self, PyObject *arg), (self, arg),
            (PyObject * self, PyObject *arg), is_node(arg, state_of(self)) ? (NodeObject *)arg : NULL, NULL) {
    PyObject *handle;
    th_node *parent;
    Py_ssize_t index;
    if (reference_parent(self, arg, &handle, &parent, &index) < 0) {
        return NULL;
    }
    set_start_bp((RangeObject *)self, handle, parent, index);
    Py_RETURN_NONE;
}

TH_NODE_API(static, PyObject *, range_set_start_after, (PyObject * self, PyObject *arg), (self, arg),
            (PyObject * self, PyObject *arg), is_node(arg, state_of(self)) ? (NodeObject *)arg : NULL, NULL) {
    PyObject *handle;
    th_node *parent;
    Py_ssize_t index;
    if (reference_parent(self, arg, &handle, &parent, &index) < 0) {
        return NULL;
    }
    set_start_bp((RangeObject *)self, handle, parent, index + 1);
    Py_RETURN_NONE;
}

TH_NODE_API(static, PyObject *, range_set_end_before, (PyObject * self, PyObject *arg), (self, arg),
            (PyObject * self, PyObject *arg), is_node(arg, state_of(self)) ? (NodeObject *)arg : NULL, NULL) {
    PyObject *handle;
    th_node *parent;
    Py_ssize_t index;
    if (reference_parent(self, arg, &handle, &parent, &index) < 0) {
        return NULL;
    }
    set_end_bp((RangeObject *)self, handle, parent, index);
    Py_RETURN_NONE;
}

TH_NODE_API(static, PyObject *, range_set_end_after, (PyObject * self, PyObject *arg), (self, arg),
            (PyObject * self, PyObject *arg), is_node(arg, state_of(self)) ? (NodeObject *)arg : NULL, NULL) {
    PyObject *handle;
    th_node *parent;
    Py_ssize_t index;
    if (reference_parent(self, arg, &handle, &parent, &index) < 0) {
        return NULL;
    }
    set_end_bp((RangeObject *)self, handle, parent, index + 1);
    Py_RETURN_NONE;
}

TH_NODE_API(static, PyObject *, range_select_node, (PyObject * self, PyObject *arg), (self, arg),
            (PyObject * self, PyObject *arg), is_node(arg, state_of(self)) ? (NodeObject *)arg : NULL, NULL) {
    PyObject *handle;
    th_node *parent;
    Py_ssize_t index;
    if (reference_parent(self, arg, &handle, &parent, &index) < 0) {
        return NULL;
    }
    store_start((RangeObject *)self, handle, parent, index);
    store_end((RangeObject *)self, handle, parent, index + 1);
    Py_RETURN_NONE;
}

TH_NODE_API(static, PyObject *, range_select_node_contents, (PyObject * self, PyObject *arg), (self, arg),
            (PyObject * self, PyObject *arg), is_node(arg, state_of(self)) ? (NodeObject *)arg : NULL, NULL) {
    PyObject *handle;
    th_node *node;
    if (node_arg(self, arg, &handle, &node) < 0) {
        return NULL;
    }
    if (node->type == TH_NODE_DOCTYPE) {
        PyErr_SetString(PyExc_ValueError, "cannot select the contents of a doctype");
        return NULL;
    }
    store_start((RangeObject *)self, handle, node, 0);
    store_end((RangeObject *)self, handle, node, node_length((HandleObject *)handle, node));
    Py_RETURN_NONE;
}

static PyObject *range_collapse(PyObject *self, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"to_start", NULL};
    int to_start = 0;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|p:collapse", keywords, &to_start)) {
        return NULL;
    }
    RangeObject *range = (RangeObject *)self;
    if (to_start) {
        store_end(range, range->start_handle, range->start_node, range->start_offset);
    } else {
        store_start(range, range->end_handle, range->end_node, range->end_offset);
    }
    Py_RETURN_NONE;
}

/* --- comparison --- */

static PyObject *range_compare_boundary_points(PyObject *self, PyObject *args) {
    int how;
    PyObject *source;
    module_state *state = state_of(self);
    if (!PyArg_ParseTuple(args, "iO!", &how, (PyTypeObject *)state->range_type, &source)) {
        return NULL;
    }
    if (how < 0 || how > 3) {
        PyErr_SetString(PyExc_ValueError, "how must be one of START_TO_START, START_TO_END, END_TO_END, END_TO_START");
        return NULL;
    }
    RangeObject *left = (RangeObject *)self;
    RangeObject *right = (RangeObject *)source;
    if (!same_root(left->start_handle, left->start_node, right->start_handle, right->start_node)) {
        PyErr_SetString(PyExc_ValueError, "the two ranges are in different trees");
        return NULL;
    }
    th_node *left_node;
    Py_ssize_t left_offset;
    th_node *right_node;
    Py_ssize_t right_offset;
    if (how == 0) {
        left_node = left->start_node;
        left_offset = left->start_offset;
        right_node = right->start_node;
        right_offset = right->start_offset;
    } else if (how == 1) {
        left_node = left->end_node;
        left_offset = left->end_offset;
        right_node = right->start_node;
        right_offset = right->start_offset;
    } else if (how == 2) {
        left_node = left->end_node;
        left_offset = left->end_offset;
        right_node = right->end_node;
        right_offset = right->end_offset;
    } else {
        left_node = left->start_node;
        left_offset = left->start_offset;
        right_node = right->end_node;
        right_offset = right->end_offset;
    }
    long order;
    Py_BEGIN_CRITICAL_SECTION(left->start_handle);
    order = bp_compare((HandleObject *)left->start_handle, left_node, left_offset, right_node, right_offset);
    Py_END_CRITICAL_SECTION();
    return PyLong_FromLong(order);
}

/* Parse and validate a (node, offset) point argument against the range's own tree. Returns 0, or
   -1 with an exception; *outside is set true when the point is in another tree (for the callers
   that treat that as a plain false rather than an error). A range whose boundaries an edit split
   across roots has no one tree to test the point against, so that is an error for every caller. */
static int point_arg(PyObject *self, PyObject *args, th_node **node, Py_ssize_t *offset, int *outside) {
    PyObject *container;
    if (!PyArg_ParseTuple(args, "On", &container, offset)) {
        return -1;
    }
    PyObject *handle;
    if (node_arg(self, container, &handle, node) < 0) {
        return -1;
    }
    RangeObject *range = (RangeObject *)self;
    *outside = !same_root(handle, *node, range->start_handle, range->start_node);
    return 0;
}

TH_NODE_API(static, PyObject *, range_compare_point, (PyObject * self, PyObject *args), (self, args),
            (PyObject * self, PyObject *args), node_argument(state_of(self), args, NULL, 0, NULL), NULL) {
    th_node *node;
    Py_ssize_t offset;
    int outside;
    if (point_arg(self, args, &node, &offset, &outside) < 0) {
        return NULL;
    }
    if (outside) {
        PyErr_SetString(PyExc_ValueError, "the point is in a different tree");
        return NULL;
    }
    RangeObject *range = (RangeObject *)self;
    HandleObject *handle = (HandleObject *)range->start_handle;
    if (validate_boundary(range->start_handle, node, offset) < 0) {
        return NULL;
    }
    if (bp_compare(handle, node, offset, range->start_node, range->start_offset) < 0) {
        return PyLong_FromLong(-1);
    }
    if (bp_compare(handle, node, offset, range->end_node, range->end_offset) > 0) {
        return PyLong_FromLong(1);
    }
    return PyLong_FromLong(0);
}

TH_NODE_API(static, PyObject *, range_is_point_in_range, (PyObject * self, PyObject *args), (self, args),
            (PyObject * self, PyObject *args), node_argument(state_of(self), args, NULL, 0, NULL), NULL) {
    th_node *node;
    Py_ssize_t offset;
    int outside;
    if (point_arg(self, args, &node, &offset, &outside) < 0) {
        return NULL;
    }
    if (outside) {
        Py_RETURN_FALSE;
    }
    RangeObject *range = (RangeObject *)self;
    HandleObject *handle = (HandleObject *)range->start_handle;
    if (validate_boundary(range->start_handle, node, offset) < 0) {
        return NULL;
    }
    if (bp_compare(handle, node, offset, range->start_node, range->start_offset) < 0 ||
        bp_compare(handle, node, offset, range->end_node, range->end_offset) > 0) {
        Py_RETURN_FALSE;
    }
    Py_RETURN_TRUE;
}

TH_NODE_API(static, PyObject *, range_intersects_node, (PyObject * self, PyObject *arg), (self, arg),
            (PyObject * self, PyObject *arg), is_node(arg, state_of(self)) ? (NodeObject *)arg : NULL, NULL) {
    PyObject *handle;
    th_node *node;
    if (node_arg(self, arg, &handle, &node) < 0) {
        return NULL;
    }
    RangeObject *range = (RangeObject *)self;
    if (!same_root(handle, node, range->start_handle, range->start_node)) {
        Py_RETURN_FALSE;
    }
    if (node->parent == NULL) {
        Py_RETURN_TRUE;
    }
    Py_ssize_t offset = sibling_index((HandleObject *)handle, node);
    if (bp_compare((HandleObject *)handle, node->parent, offset, range->end_node, range->end_offset) < 0 &&
        bp_compare((HandleObject *)handle, node->parent, offset + 1, range->start_node, range->start_offset) > 0) {
        Py_RETURN_TRUE;
    }
    Py_RETURN_FALSE;
}

/* --- content operations --- */

/* Run the extract algorithm and collapse the range; return the fragment node (already wrapped) or,
   when discard is set (deleteContents), None. */
static PyObject *extract_or_delete(PyObject *self, int discard) {
    RangeObject *range = (RangeObject *)self;
    HandleObject *handle = (HandleObject *)range->start_handle;
    module_state *state = state_of(self);
    PyObject *result = NULL;
    Py_BEGIN_CRITICAL_SECTION(range->start_handle);
    if (range_check_recursive_depth(range, discard ? "delete_contents()" : "extract_contents()") == 0) {
        range_drop_index(handle);
        th_node *common = common_ancestor(range->start_node, range->end_node);
        th_node *new_node;
        Py_ssize_t new_offset;
        int collapsed = range_is_collapsed(range);
        if (!collapsed) {
            collapse_point(handle, common, range->start_node, range->start_offset, &new_node, &new_offset);
        }
        th_node *fragment =
            do_extract(handle, common, range->start_node, range->start_offset, range->end_node, range->end_offset);
        if (fragment == NULL && !PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: a node allocation failed */
            PyErr_NoMemory();                        /* GCOVR_EXCL_LINE: allocation failure */
        } /* GCOVR_EXCL_LINE: llvm-cov assigns this line to the allocation-failure edge */
        if (fragment != NULL) {
            if (!collapsed) {
                store_start(range, range->start_handle, new_node, new_offset);
                store_end(range, range->start_handle, new_node, new_offset);
            }
            result = discard ? Py_NewRef(Py_None) : node_wrap(state, range->start_handle, fragment);
        }
    }
    Py_END_CRITICAL_SECTION();
    return result;
}

static PyObject *range_extract_contents(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return extract_or_delete(self, 0);
}

static PyObject *range_delete_contents(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return extract_or_delete(self, 1);
}

static PyObject *range_clone_contents(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    RangeObject *range = (RangeObject *)self;
    module_state *state = state_of(self);
    PyObject *result = NULL;
    Py_BEGIN_CRITICAL_SECTION(range->start_handle);
    if (range_check_recursive_depth(range, "clone_contents()") == 0) {
        th_node *common = common_ancestor(range->start_node, range->end_node);
        th_node *fragment = do_clone((HandleObject *)range->start_handle, common, range->start_node,
                                     range->start_offset, range->end_node, range->end_offset);
        if (fragment == NULL && !PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: a node allocation failed */
            PyErr_NoMemory();                        /* GCOVR_EXCL_LINE: allocation failure */
        } /* GCOVR_EXCL_LINE: llvm-cov assigns this line to the allocation-failure edge */
        if (fragment != NULL) {
            result = node_wrap(state, range->start_handle, fragment);
        }
    }
    Py_END_CRITICAL_SECTION();
    return result;
}

/* Detach a node argument, already in the range's tree (insert_core imports a foreign one first),
   from wherever it lives and return it. NULL with an exception on a Document or a cycle. */
static th_node *adopt(RangeObject *range, PyObject *child_obj) {
    NodeObject *child = (NodeObject *)child_obj;
    if (child->node->type == TH_NODE_DOCUMENT) {
        PyErr_SetString(PyExc_ValueError, "a Document cannot be inserted");
        return NULL;
    }
    if (th_node_contains(((HandleObject *)range->start_handle)->tree, child->node, range->start_node)) {
        PyErr_SetString(PyExc_ValueError, "cannot insert a node into its own subtree");
        return NULL;
    }
    if (child->node->type != TH_NODE_CONTENT) { /* a fragment stays where it is; only its children move */
        range_remove((HandleObject *)range->start_handle, child->node);
    }
    return child->node;
}

/* The hierarchy rules for inserting incoming into parent before child: a DocumentFragment is checked as its children,
   which are what the insertion places. Sets a ValueError and returns -1 on a violation. */
static int reject_insert(th_node *parent, th_node *incoming, th_node *child) {
    Py_ssize_t count = 1;
    for (th_node *walk = incoming->first_child; incoming->type == TH_NODE_CONTENT && walk != NULL;
         walk = walk->next_sibling) {
        count++;
    }
    th_node **nodes = PyMem_Malloc((size_t)count * sizeof(th_node *));
    if (nodes == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    count = 0;
    if (incoming->type == TH_NODE_CONTENT) {
        for (th_node *walk = incoming->first_child; walk != NULL; walk = walk->next_sibling) {
            nodes[count++] = walk;
        }
    } else {
        nodes[count++] = incoming;
    }
    const char *misplaced = th_pre_insert_error(parent, nodes, count, child, NULL, NULL);
    PyMem_Free(nodes);
    if (misplaced != NULL) {
        PyErr_SetString(PyExc_ValueError, misplaced);
        return -1;
    }
    return 0;
}

/* The body of insert_core over an owned node reference the import may swap for a local copy. */
static th_node *insert_at_start(RangeObject *range, PyObject **node_ref) {
    th_node *start_node;
    int start_text_like;
    PyObject *node_obj = *node_ref;
    for (;;) {
        start_node = range->start_node;
        start_text_like = start_node->type == TH_NODE_TEXT || start_node->type == TH_NODE_CDATA;
        /* the lock guards a wrapper's node only while it belongs to this tree, and a node of another tree is never the
           start node */
        if (start_node->type == TH_NODE_COMMENT || start_node->type == TH_NODE_PI ||
            (start_text_like && start_node->parent == NULL) ||
            (node_owned_by((NodeObject *)node_obj, range->start_handle) &&
             ((NodeObject *)node_obj)->node == start_node)) {
            PyErr_SetString(PyExc_ValueError, "cannot insert at this boundary point");
            return NULL;
        }
        int imported = range_import((HandleObject *)range->start_handle, node_ref);
        node_obj = *node_ref;
        if (imported < 0) { /* GCOVR_EXCL_BR_LINE: OOM only */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (imported == 0) {
            break;
        }
    }
    Py_ssize_t start_offset = range->start_offset;
    HandleObject *handle = (HandleObject *)range->start_handle;
    th_node *incoming = ((NodeObject *)node_obj)->node;
    th_node *reference = start_text_like ? start_node : child_at(handle, start_node, start_offset);
    th_node *parent = reference == NULL ? start_node : reference->parent;
    /* a Text start is split and the node goes after its head, which is before the head's current next sibling */
    if (reject_insert(parent, incoming, start_text_like ? start_node->next_sibling : reference) < 0) {
        return NULL;
    }
    range_drop_index(handle);
    if (start_text_like) {
        /* a cursor on the start node keeps the tail the split links after it next to a cursor; the tail's offset is
           looked up below */
        (void)sibling_index(handle, start_node);
        reference = split_data_node(handle, start_node, start_offset);
        if (reference == NULL) { /* GCOVR_EXCL_BR_LINE: split only fails on OOM */
            return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    if (incoming == reference) {
        reference = reference->next_sibling;
    }
    th_node *linked = adopt(range, node_obj);
    if (linked == NULL) {
        return NULL;
    }
    Py_ssize_t new_offset = reference == NULL ? child_count(handle, parent) : sibling_index(handle, reference);
    new_offset += linked->type == TH_NODE_CONTENT ? child_count(handle, linked) : 1;
    th_tree_note_deepened(handle->tree);
    if (linked->type == TH_NODE_CONTENT) {
        /* a DocumentFragment inserts its children and is left empty */
        while (linked->first_child != NULL) {
            th_node *child = linked->first_child;
            range_remove(handle, child);
            range_insert_before(handle, parent, child, reference);
        }
    } else {
        range_insert_before(handle, parent, linked, reference);
    }
    if (range_is_collapsed(range)) {
        store_end(range, range->start_handle, parent, new_offset);
    }
    return linked;
}

/* The DOM insert algorithm; returns the inserted th_node in the range's tree (for a DocumentFragment,
   the now-empty fragment whose children moved in), or NULL on error. The caller holds the range
   handle's critical section. Importing a foreign node suspends that section (see import_node), so
   the boundary is re-validated until a pass imports nothing; the first check comes before any
   import, so a bad boundary raises without moving the node. */
static th_node *insert_core(RangeObject *range, PyObject *node_arg) {
    if (!is_node(node_arg, state_of((PyObject *)range))) {
        PyErr_SetString(PyExc_TypeError, "expected a node");
        return NULL;
    }
    PyObject *node_obj = Py_NewRef(node_arg);
    th_node *linked = insert_at_start(range, &node_obj);
    Py_DECREF(node_obj);
    return linked;
}

static PyObject *range_insert_node(PyObject *self, PyObject *node_obj) {
    RangeObject *range = (RangeObject *)self;
    th_node *linked;
    Py_BEGIN_CRITICAL_SECTION(range->start_handle);
    linked = insert_core(range, node_obj);
    Py_END_CRITICAL_SECTION();
    if (linked == NULL) {
        return NULL;
    }
    Py_RETURN_NONE;
}

/* surroundContents forbids a range partially containing any non-Text node: sets a ValueError and returns -1 then. The
   nodes a range partially contains are its two boundary paths below the common ancestor. */
static int reject_partial_non_text(RangeObject *range) {
    th_node *common = common_ancestor(range->start_node, range->end_node);
    int partial_non_text = 0;
    for (th_node *node = range->start_node; node != common; node = node->parent) {
        TH_WORK_STEP();
        partial_non_text |= node->type != TH_NODE_TEXT;
    }
    for (th_node *node = range->end_node; node != common; node = node->parent) {
        TH_WORK_STEP();
        partial_non_text |= node->type != TH_NODE_TEXT;
    }
    if (partial_non_text) {
        PyErr_SetString(PyExc_ValueError, "the range partially contains a non-Text node");
        return -1;
    }
    return 0;
}

/* surroundContents' checks, then new_parent imported into the range's tree before the extract
   mutates it. An import suspends the caller's critical section, so the checks repeat until a pass
   imports nothing. Returns 0, or -1 with an exception. */
static int import_surround_parent(RangeObject *range, PyObject *new_parent) {
    for (;;) {
        if (reject_partial_non_text(range) < 0) {
            return -1;
        }
        int imported = range_import((HandleObject *)range->start_handle, &new_parent);
        if (imported <= 0) {
            return (int)imported;
        }
    }
}

TH_NODE_API(static, PyObject *, range_surround_contents, (PyObject * self, PyObject *new_parent), (self, new_parent),
            (PyObject * self, PyObject *new_parent),
            is_node(new_parent, state_of(self)) ? (NodeObject *)new_parent : NULL, NULL) {
    RangeObject *range = (RangeObject *)self;
    module_state *state = state_of(self);
    if (!is_node(new_parent, state)) {
        PyErr_SetString(PyExc_TypeError, "expected a node");
        return NULL;
    }
    th_node *parent_node = ((NodeObject *)new_parent)->node;
    /* A document, doctype, or fragment is rejected by the DOM up front, and character data or a processing
       instruction fails the final append of the extracted content; checking both here keeps the tree unchanged. */
    if (parent_node->type != TH_NODE_ELEMENT) {
        PyErr_SetString(PyExc_ValueError, "the wrapper must be an element");
        return NULL;
    }
    PyObject *result = NULL;
    HandleObject *handle = (HandleObject *)range->start_handle;
    Py_BEGIN_CRITICAL_SECTION(range->start_handle);
    if (import_surround_parent(range, new_parent) == 0) {
        parent_node = ((NodeObject *)new_parent)->node; /* the import re-points a foreign wrapper at its copy */
        range_drop_index(handle);
        th_node *common = common_ancestor(range->start_node, range->end_node);
        th_node *new_node;
        Py_ssize_t new_offset;
        int collapsed = range_is_collapsed(range);
        if (!collapsed) {
            collapse_point(handle, common, range->start_node, range->start_offset, &new_node, &new_offset);
        }
        th_node *fragment =
            do_extract(handle, common, range->start_node, range->start_offset, range->end_node, range->end_offset);
        if (fragment == NULL && !PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: a node allocation failed */
            PyErr_NoMemory();                        /* GCOVR_EXCL_LINE: allocation failure */
        } /* GCOVR_EXCL_LINE: llvm-cov assigns this line to the allocation-failure edge */
        if (fragment != NULL) {
            if (!collapsed) {
                store_start(range, range->start_handle, new_node, new_offset);
                store_end(range, range->start_handle, new_node, new_offset);
            }
            while (parent_node->first_child != NULL) {
                range_remove(handle, parent_node->first_child);
            }
            th_node *linked = insert_core(range, new_parent);
            if (linked != NULL) {
                adopt_fragment_children(handle, linked, fragment);
                Py_ssize_t index = sibling_index(handle, linked);
                store_start(range, range->start_handle, linked->parent, index);
                store_end(range, range->start_handle, linked->parent, index + 1);
                result = Py_NewRef(new_parent);
            }
        }
    }
    Py_END_CRITICAL_SECTION();
    return result;
}

static PyObject *range_clone_range(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    RangeObject *range = (RangeObject *)self;
    PyTypeObject *type = Py_TYPE(self);
    RangeObject *copy = (RangeObject *)type->tp_alloc(type, 0);
    if (copy == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    copy->start_handle = Py_NewRef(range->start_handle);
    copy->end_handle = Py_NewRef(range->end_handle);
    copy->start_node = range->start_node;
    copy->end_node = range->end_node;
    copy->start_offset = range->start_offset;
    copy->end_offset = range->end_offset;
    live_track(copy, ((HandleObject *)range->start_handle)->tree);
    return (PyObject *)copy;
}

static PyObject *range_repr(PyObject *self) {
    RangeObject *range = (RangeObject *)self;
    module_state *state = state_of(self);
    PyObject *start = node_wrap(state, range->start_handle, range->start_node);
    PyObject *end = node_wrap(state, range->end_handle, range->end_node);
    if (start == NULL || end == NULL) { /* GCOVR_EXCL_BR_LINE: node_wrap only fails on OOM */
        Py_XDECREF(start);              /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_XDECREF(end);                /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result =
        th_str_format("<Range start=(%R, %zd) end=(%R, %zd)>", start, range->start_offset, end, range->end_offset);
    Py_DECREF(start);
    Py_DECREF(end);
    return result;
}

/* --- StaticRange --- */

TH_NODE_API(static, PyObject *, static_range_new, (PyTypeObject * type, PyObject *args, PyObject *kwds),
            (type, args, kwds), (PyTypeObject * type, PyObject *args, PyObject *kwds),
            node_argument(PyType_GetModuleState(type), args, kwds, 0, "start_container"),
            node_argument(PyType_GetModuleState(type), args, kwds, 2, "end_container")) {
    static char *keywords[] = {"start_container", "start_offset", "end_container", "end_offset", NULL};
    PyObject *start_container;
    Py_ssize_t start_offset;
    PyObject *end_container;
    Py_ssize_t end_offset;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OnOn:StaticRange", keywords, &start_container, &start_offset,
                                     &end_container, &end_offset)) {
        return NULL;
    }
    module_state *state = PyType_GetModuleState(type);
    if (!is_node(start_container, state) || !is_node(end_container, state)) {
        PyErr_SetString(PyExc_TypeError, "the containers must be nodes");
        return NULL;
    }
    th_node *start_node = ((NodeObject *)start_container)->node;
    th_node *end_node = ((NodeObject *)end_container)->node;
    if (start_node->type == TH_NODE_DOCTYPE || end_node->type == TH_NODE_DOCTYPE) {
        PyErr_SetString(PyExc_ValueError, "a StaticRange boundary cannot be a doctype");
        return NULL;
    }
    RangeObject *self = (RangeObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->start_handle = Py_NewRef(((NodeObject *)start_container)->handle);
    self->end_handle = Py_NewRef(((NodeObject *)end_container)->handle);
    self->start_node = start_node;
    self->end_node = end_node;
    self->start_offset = start_offset;
    self->end_offset = end_offset;
    return (PyObject *)self;
}

/* --- type registration --- */

static PyGetSetDef abstract_range_getset[] = {
    {"start_container", get_start_container, NULL, "the node the range starts in", NULL},
    {"start_offset", get_start_offset, NULL, "the offset of the start boundary within its container", NULL},
    {"end_container", get_end_container, NULL, "the node the range ends in", NULL},
    {"end_offset", get_end_offset, NULL, "the offset of the end boundary within its container", NULL},
    {"collapsed", get_collapsed, NULL, "whether the two boundary points coincide", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

static PyGetSetDef range_getset[] = {
    {"start_container", get_start_container, NULL, "the node the range starts in", NULL},
    {"start_offset", get_start_offset, NULL, "the offset of the start boundary within its container", NULL},
    {"end_container", get_end_container, NULL, "the node the range ends in", NULL},
    {"end_offset", get_end_offset, NULL, "the offset of the end boundary within its container", NULL},
    {"collapsed", get_collapsed, NULL, "whether the two boundary points coincide", NULL},
    {"common_ancestor_container", get_common_ancestor, NULL, "the deepest node containing both boundary points", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

PyDoc_STRVAR(set_start_doc, "set_start(container, offset, /)\n--\n\nSet the start boundary to (container, offset).");
PyDoc_STRVAR(set_end_doc, "set_end(container, offset, /)\n--\n\nSet the end boundary to (container, offset).");
PyDoc_STRVAR(set_start_before_doc, "set_start_before(node, /)\n--\n\nSet the start boundary just before node.");
PyDoc_STRVAR(set_start_after_doc, "set_start_after(node, /)\n--\n\nSet the start boundary just after node.");
PyDoc_STRVAR(set_end_before_doc, "set_end_before(node, /)\n--\n\nSet the end boundary just before node.");
PyDoc_STRVAR(set_end_after_doc, "set_end_after(node, /)\n--\n\nSet the end boundary just after node.");
PyDoc_STRVAR(select_node_doc, "select_node(node, /)\n--\n\nSet the range to select node itself.");
PyDoc_STRVAR(select_node_contents_doc, "select_node_contents(node, /)\n--\n\nSet the range to select node's children.");
PyDoc_STRVAR(collapse_doc, "collapse(to_start=False, /)\n--\n\nCollapse the range onto one of its boundaries.");
PyDoc_STRVAR(compare_boundary_points_doc,
             "compare_boundary_points(how, source_range, /)\n--\n\nCompare a boundary of this range with one of "
             "source_range; returns -1, 0, or 1.");
PyDoc_STRVAR(compare_point_doc,
             "compare_point(node, offset, /)\n--\n\nWhere (node, offset) lies relative to the range: -1, 0, or 1.");
PyDoc_STRVAR(is_point_in_range_doc,
             "is_point_in_range(node, offset, /)\n--\n\nWhether (node, offset) is within the range.");
PyDoc_STRVAR(intersects_node_doc, "intersects_node(node, /)\n--\n\nWhether node overlaps the range.");
PyDoc_STRVAR(clone_contents_doc, "clone_contents()\n--\n\n"
                                 "Copy the range's content into a new fragment.\n\n"
                                 ":raises RecursionError: if a partial boundary path is 400 levels or deeper.");
PyDoc_STRVAR(extract_contents_doc,
             "extract_contents()\n--\n\n"
             "Move the range's content into a new fragment.\n\n"
             ":raises RecursionError: if a partial boundary path is 400 levels or deeper; the tree is unchanged.");
PyDoc_STRVAR(delete_contents_doc,
             "delete_contents()\n--\n\n"
             "Remove the range's content from the tree.\n\n"
             ":raises RecursionError: if a partial boundary path is 400 levels or deeper; the tree is unchanged.");
PyDoc_STRVAR(insert_node_doc, "insert_node(node, /)\n--\n\nInsert node at the start of the range.");
PyDoc_STRVAR(surround_contents_doc, "surround_contents(new_parent, /)\n--\n\n"
                                    "Wrap the range's content in new_parent.\n\n"
                                    ":raises ValueError: if new_parent is not an element; the tree is unchanged.");
PyDoc_STRVAR(clone_range_doc, "clone_range()\n--\n\nReturn a new Range with the same boundary points.");

static PyMethodDef range_methods[] = {
    {"set_start", range_set_start, METH_VARARGS, set_start_doc},
    {"set_end", range_set_end, METH_VARARGS, set_end_doc},
    {"set_start_before", range_set_start_before, METH_O, set_start_before_doc},
    {"set_start_after", range_set_start_after, METH_O, set_start_after_doc},
    {"set_end_before", range_set_end_before, METH_O, set_end_before_doc},
    {"set_end_after", range_set_end_after, METH_O, set_end_after_doc},
    {"select_node", range_select_node, METH_O, select_node_doc},
    {"select_node_contents", range_select_node_contents, METH_O, select_node_contents_doc},
    {"collapse", (PyCFunction)(void (*)(void))range_collapse, METH_VARARGS | METH_KEYWORDS, collapse_doc},
    {"compare_boundary_points", range_compare_boundary_points, METH_VARARGS, compare_boundary_points_doc},
    {"compare_point", range_compare_point, METH_VARARGS, compare_point_doc},
    {"is_point_in_range", range_is_point_in_range, METH_VARARGS, is_point_in_range_doc},
    {"intersects_node", range_intersects_node, METH_O, intersects_node_doc},
    {"clone_contents", range_clone_contents, METH_NOARGS, clone_contents_doc},
    {"extract_contents", range_extract_contents, METH_NOARGS, extract_contents_doc},
    {"delete_contents", range_delete_contents, METH_NOARGS, delete_contents_doc},
    {"insert_node", range_insert_node, METH_O, insert_node_doc},
    {"surround_contents", range_surround_contents, METH_O, surround_contents_doc},
    {"clone_range", range_clone_range, METH_NOARGS, clone_range_doc},
    {NULL, NULL, 0, NULL},
};

PyDoc_STRVAR(range_doc, "A live DOM Range between two boundary points.\n\n"
                        ":param container: the node both boundaries start collapsed in.\n"
                        ":param offset: the shared start offset (default 0).");

static PyType_Slot range_slots[] = {
    {Py_tp_doc, (void *)range_doc},
    {Py_tp_new, range_new},
    {Py_tp_dealloc, range_dealloc},
    {Py_tp_repr, range_repr},
    {Py_tp_getset, range_getset},
    {Py_tp_methods, range_methods},
    {0, NULL},
};

static PyType_Spec range_spec = {
    .name = "turbohtml._html.Range",
    .basicsize = sizeof(RangeObject),
    .flags = Py_TPFLAGS_DEFAULT,
    .slots = range_slots,
};

PyDoc_STRVAR(static_range_doc, "An immutable snapshot of two boundary points.\n\n"
                               ":param start_container: the node the range starts in.\n"
                               ":param start_offset: the start offset.\n"
                               ":param end_container: the node the range ends in.\n"
                               ":param end_offset: the end offset.");

static PyType_Slot static_range_slots[] = {
    {Py_tp_doc, (void *)static_range_doc},
    {Py_tp_new, static_range_new},
    {Py_tp_dealloc, range_dealloc},
    {Py_tp_getset, abstract_range_getset},
    {0, NULL},
};

static PyType_Spec static_range_spec = {
    .name = "turbohtml._html.StaticRange",
    .basicsize = sizeof(RangeObject),
    .flags = Py_TPFLAGS_DEFAULT,
    .slots = static_range_slots,
};

/* Publish an integer class constant (the compare_boundary_points modes) on the Range type. */
static int add_int_constant(PyObject *type, const char *name, long value) {
    PyObject *number = PyLong_FromLong(value);
    if (number == NULL) { /* GCOVR_EXCL_BR_LINE: a small int always allocates */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int rc = PyObject_SetAttrString(type, name, number);
    Py_DECREF(number);
    return rc;
}

int range_register(PyObject *module, module_state *state) {
    state->range_type = PyType_FromModuleAndSpec(module, &range_spec, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->range_type == NULL ||                                     /* GCOVR_EXCL_BR_LINE */
        PyModule_AddObjectRef(module, "Range", state->range_type) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1;                                                       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* the four compare_boundary_points modes, mirrored from the DOM Range constants */
    if (add_int_constant(state->range_type, "START_TO_START", 0) < 0 || /* GCOVR_EXCL_BR_LINE: OOM only */
        add_int_constant(state->range_type, "START_TO_END", 1) < 0 ||   /* GCOVR_EXCL_BR_LINE: OOM only */
        add_int_constant(state->range_type, "END_TO_END", 2) < 0 ||     /* GCOVR_EXCL_BR_LINE: OOM only */
        add_int_constant(state->range_type, "END_TO_START", 3) < 0) {   /* GCOVR_EXCL_BR_LINE: OOM only */
        return -1;                                                      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->static_range_type = PyType_FromModuleAndSpec(module, &static_range_spec, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->static_range_type == NULL ||                                           /* GCOVR_EXCL_BR_LINE */
        PyModule_AddObjectRef(module, "StaticRange", state->static_range_type) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1;                                                                    /* GCOVR_EXCL_LINE: OOM path */
    }
    return 0;
}
