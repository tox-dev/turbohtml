/* Build and edit the node tree by hand: create elements/text/comments and
   rearrange them, the C side of the construction and mutation API.

   These functions back the mutable Python Node API (dom/node.c, dom/element.c):
   th_tree_new starts an empty arena-owned tree, the th_tree_make_* builders add
   nodes, and the th_node_* primitives set data, edit attributes, and relink the
   tree. They share the arena, node allocator and sibling linkers with the parser
   (tree.c) through dom/tree_internal.h, so a hand-built node is indistinguishable
   from a parsed one. */

#include "dom/tree.h"
#include "dom/tree_internal.h" /* arena_alloc, need_text, node_new, node_append/remove/insert_before, intern_attr_dynamic */
#include "dom/observe.h"       /* th_mo_* mutation-record hooks */

#include "core/ascii.h" /* lower_ascii for the foreign case-insensitive attribute scan */
#include "core/vec.h"   /* th_grow_cap for the shadow-table growth */

#include <string.h>

/* An empty tree for programmatically constructed nodes: the arena grows on the
   first allocation and can_span stays 0, so a node's text is always owned rather
   than a borrowed span. */
th_tree *th_tree_new(void) {
    return PyMem_Calloc(1, sizeof(th_tree));
}

void th_tree_set_quirks(th_tree *tree, int quirks) {
    tree->quirks = quirks;
}

int th_tree_is_xml(const th_tree *tree) {
    return tree->xml;
}

void th_tree_set_xml(th_tree *tree, int xml) {
    tree->xml = xml;
}

/* Construct an empty document-fragment node in tree's arena: the container the
   Range content operations (dom/range.c) fill and hand back. */
th_node *th_tree_make_fragment(th_tree *tree) {
    return node_new(tree, TH_NODE_CONTENT);
}

/* Shadow DOM linkage. A shadow root is a fragment node flagged TH_SHADOW_ROOT and held
   off the light tree; the per-tree shadow table is the only path from a host to its
   shadow root and back. The slot-assignment and flattened-tree algorithms that read
   these live in dom/shadow.c, which reaches the table only through these accessors. */
int th_node_is_shadow_root(const th_node *node) {
    return node->type == TH_NODE_CONTENT && (node->tag_flags & TH_SHADOW_ROOT) != 0;
}

int th_shadow_mode(const th_node *root) {
    return (root->tag_flags & TH_SHADOW_CLOSED) != 0;
}

th_node *th_element_attach_shadow(th_tree *tree, th_node *host, int mode) {
    th_node *root = th_tree_make_fragment(tree);
    if (root == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    root->tag_flags = (uint8_t)(TH_SHADOW_ROOT | (mode ? TH_SHADOW_CLOSED : 0u));
    if (tree->shadow_count == tree->shadow_cap) {
        size_t cap, bytes;
        /* the count cannot overflow size_t, so the grow guard never trips */
        int fits = th_grow_cap((size_t)tree->shadow_count + 1, (size_t)tree->shadow_cap, 4, sizeof(th_shadow_link),
                               &cap, &bytes);
        if (!fits) {     /* GCOVR_EXCL_BR_LINE: overflow-guard path, unreachable from a test */
            return NULL; /* GCOVR_EXCL_LINE: overflow-guard path */
        }
        th_shadow_link *grown = PyMem_Realloc(tree->shadows, bytes);
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        tree->shadows = grown;
        tree->shadow_cap = (Py_ssize_t)cap;
    }
    if (th_node_map_reserve(&tree->shadow_index, tree->shadow_index.count + 2) < 0) { /* GCOVR_EXCL_BR_LINE: OOM */
        return NULL;                                                                  /* GCOVR_EXCL_LINE */
    }
    (void)th_node_map_insert(&tree->shadow_index, host, tree->shadow_count + 1);
    (void)th_node_map_insert(&tree->shadow_index, root, tree->shadow_count + 1);
    tree->shadows[tree->shadow_count].host = host;
    tree->shadows[tree->shadow_count].root = root;
    tree->shadow_count++;
    return root;
}

th_node *th_element_shadow_root(th_tree *tree, th_node *host) {
    if (host->type != TH_NODE_ELEMENT) {
        return NULL;
    }
    Py_ssize_t index = th_node_map_find(&tree->shadow_index, host);
    return index == 0 ? NULL : tree->shadows[index - 1].root;
}

th_node *th_shadow_host(th_tree *tree, th_node *root) {
    return tree->shadows[th_node_map_find(&tree->shadow_index, root) - 1].host;
}

/* Materialize a character-data node's text in place (a parsed text node borrows a
   source span until realized) and return the owned code-point buffer, so the Range
   operations can slice node->text directly. */
Py_UCS4 *th_node_realize_text(th_tree *tree, th_node *node) {
    return need_text(tree, node);
}

/* Construct a text/comment/doctype node owning a copy of data in tree's arena. */
th_node *th_tree_make_data_node(th_tree *tree, int type, const Py_UCS4 *data, Py_ssize_t len) {
    th_node *node = node_new(tree, (enum th_node_type)type);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (len > 0) {
        Py_UCS4 *owned = arena_alloc(tree, len * (Py_ssize_t)sizeof(Py_UCS4));
        if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        memcpy(owned, data, (size_t)len * sizeof(Py_UCS4));
        node->text = owned;
        node->text_len = len;
    }
    return node;
}

/* Construct a processing-instruction node. The target, a space, and the data are
   packed into one buffer (target_len marks the split, kept in attr_count) so the
   node carries both halves; serialization writes "<?" + buffer + ">". */
th_node *th_tree_make_pi(th_tree *tree, const Py_UCS4 *target, Py_ssize_t target_len, const Py_UCS4 *data,
                         Py_ssize_t data_len) {
    th_node *node = node_new(tree, TH_NODE_PI);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t total = target_len + 1 + data_len;
    Py_UCS4 *owned = arena_alloc(tree, total * (Py_ssize_t)sizeof(Py_UCS4));
    if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    memcpy(owned, target, (size_t)target_len * sizeof(Py_UCS4));
    owned[target_len] = ' ';
    memcpy(owned + target_len + 1, data, (size_t)data_len * sizeof(Py_UCS4));
    node->text = owned;
    node->text_len = total;
    node->attr_count = target_len;
    return node;
}

/* Intern a UTF-8 attribute name to its atom (static table, else the tree's
   dynamic table), the construction-side counterpart of intern_attr. */
static uint32_t th_attr_intern_utf8(th_tree *tree, const char *bytes, Py_ssize_t len) {
    uint32_t atom = th_attr_atom(bytes, (size_t)len);
    if (atom != TH_ATTR_UNKNOWN) {
        return atom;
    }
    return intern_attr_dynamic(tree, bytes, len);
}

/* Construct an element node, with attr_count empty attribute slots to fill with
   th_tree_set_attr. A NULL known tag selects the generated immutable spelling. */
int th_node_rename(th_tree *tree, th_node *node, const Py_UCS4 *tag, Py_ssize_t tag_len, uint16_t atom) {
    if (tag != NULL) {
        Py_UCS4 *owned = arena_alloc(tree, tag_len * (Py_ssize_t)sizeof(Py_UCS4));
        if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        memcpy(owned, tag, (size_t)tag_len * sizeof(Py_UCS4));
        node->text = owned;
        node->text_len = tag_len;
    } else {
        node->text = (Py_UCS4 *)th_tag_wide_name(atom);
        node->text_len = th_tag_table[atom - 1].name_len;
    }
    node->atom = atom;
    th_src_loc *loc = tree->track_locations ? *node_loc(node) : NULL;
    if (loc != NULL) {
        loc->start_dirty = 1;
        loc->end_dirty = loc->has_end_tag;
    }
    /* the category bits follow the new atom; the bits recording how the source opened
       and closed the element stay, as its position in the source is unchanged */
    node->tag_flags = th_tag_flags(atom) | (node->tag_flags & (TH_ELEM_CLOSED_BY_END_TAG | TH_ELEM_IMPLIED));
    return 0;
}

th_node *th_tree_make_element(th_tree *tree, const Py_UCS4 *tag, Py_ssize_t tag_len, uint16_t atom,
                              Py_ssize_t attr_count) {
    th_node *node = node_new(tree, TH_NODE_ELEMENT);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    node->atom = atom;
    node->tag_flags = th_tag_flags(atom); /* so a constructed/unpickled raw-text element serializes literally */
    /* A built element has no source: its markup is its serialization, which closes every non-void element, so escape
       mode must reproduce that end tag. The XML parser, the one caller that reads source, records its own. */
    if (!is_void_atom(atom)) {
        node->tag_flags |= TH_ELEM_CLOSED_BY_END_TAG;
    }
    if (atom == TH_TAG_UNKNOWN || tag != NULL) {
        Py_UCS4 *owned = arena_alloc(tree, tag_len * (Py_ssize_t)sizeof(Py_UCS4));
        if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        memcpy(owned, tag, (size_t)tag_len * sizeof(Py_UCS4));
        node->text = owned;
        node->text_len = tag_len;
    } else {
        node->text = (Py_UCS4 *)th_tag_wide_name(atom);
        node->text_len = th_tag_table[atom - 1].name_len;
    }
    if (attr_count > 0) {
        node->attrs = arena_alloc(tree, attr_count * (Py_ssize_t)sizeof(th_node_attr));
        if (node->attrs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        memset(node->attrs, 0, (size_t)attr_count * sizeof(th_node_attr));
        node->attr_count = attr_count;
    }
    return node;
}

/* Fill attribute slot index on a constructed element. has_value 0 makes a
   valueless attribute (value NULL); otherwise the value is owned in the arena,
   with an empty value kept distinct from a valueless one. */
int th_tree_set_attr(th_tree *tree, th_node *node, Py_ssize_t index, const char *name, Py_ssize_t name_len,
                     const Py_UCS4 *value, Py_ssize_t value_len, int has_value) {
    th_node_attr *attr = &node->attrs[index];
    attr->name_atom = th_attr_intern_utf8(tree, name, name_len);
    if (!has_value) {
        return 0;
    }
    Py_UCS4 *owned = arena_alloc(tree, (value_len ? value_len : 1) * (Py_ssize_t)sizeof(Py_UCS4));
    if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    memcpy(owned, value, (size_t)value_len * sizeof(Py_UCS4));
    attr->value = owned;
    attr->value_len = value_len;
    return 0;
}

/* Flag a located element's start tag as changed, so the lossless serializer rewrites
   it from the current attributes rather than re-emitting the stale source span. A
   no-op unless the tree tracks source locations and the element carries one (a
   synthetic or hand-built element has none). */
static void mark_start_dirty(th_tree *tree, th_node *node) {
    if (!tree->track_locations) {
        return;
    }
    th_src_loc *loc = *node_loc(node);
    if (loc != NULL) {
        loc->start_dirty = 1;
    }
}

static int node_attr_store(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len, const Py_UCS4 *value,
                           Py_ssize_t value_len, int has_value, int append) {
    tree->attr_version++;
    mark_start_dirty(tree, node);
    uint32_t atom = th_attr_intern_utf8(tree, name, name_len);
    tree->id_version += atom == TH_ATTR_ID;
    Py_UCS4 *owned = NULL;
    if (has_value) {
        owned = arena_alloc(tree, (value_len ? value_len : 1) * (Py_ssize_t)sizeof(Py_UCS4));
        if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        memcpy(owned, value, (size_t)value_len * sizeof(Py_UCS4));
    }
    Py_ssize_t existing = append ? -1 : th_node_attr_find(tree, node, name, name_len);
    if (existing >= 0) {
        th_mo_attr_changed(tree, node, atom, node->attrs[existing].value, node->attrs[existing].value_len, 1);
    } else {
        th_mo_attr_changed(tree, node, atom, NULL, 0, 0);
    }
    if (existing >= 0) {
        node->attrs[existing].value = owned;
        node->attrs[existing].value_len = has_value ? value_len : 0;
        return 0;
    }
    size_t capacity = node->attr_capacity_shift ? (size_t)1 << node->attr_capacity_shift : 0;
    if ((size_t)node->attr_count >= capacity) {
        size_t bytes;
        int fits = th_grow_cap((size_t)node->attr_count + 1, capacity, 2, sizeof(th_node_attr), &capacity, &bytes);
        if (!fits || bytes > PY_SSIZE_T_MAX) { /* GCOVR_EXCL_BR_LINE: allocation-size overflow */
            PyErr_NoMemory();                  /* GCOVR_EXCL_LINE: allocation-size overflow */
            return -1;                         /* GCOVR_EXCL_LINE: allocation-size overflow */
        }
        th_node_attr *grown = arena_alloc(tree, (Py_ssize_t)bytes);
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (node->attr_count > 0) {
            memcpy(grown, node->attrs, (size_t)node->attr_count * sizeof(th_node_attr));
        }
        node->attrs = grown;
        node->attr_capacity_shift = 0;
        for (size_t slots = capacity; slots > 1; slots >>= 1) {
            node->attr_capacity_shift++;
        }
    }
    node->attrs[node->attr_count].name_atom = atom;
    node->attrs[node->attr_count].value = owned;
    node->attrs[node->attr_count].value_len = has_value ? value_len : 0;
    node->attr_count++;
    return 0;
}

int th_node_attr_set(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len, const Py_UCS4 *value,
                     Py_ssize_t value_len, int has_value) {
    return node_attr_store(tree, node, name, name_len, value, value_len, has_value, 0);
}

int th_node_attr_append(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len, const Py_UCS4 *value,
                        Py_ssize_t value_len, int has_value) {
    return node_attr_store(tree, node, name, name_len, value, value_len, has_value, 1);
}

/* Replace a node's character data with a copy of len code points (an empty buffer
   is stored as none). Returns 0, or -1 on allocation failure. */
int th_node_set_data(th_tree *tree, th_node *node, const Py_UCS4 *data, Py_ssize_t len) {
    const Py_UCS4 *old = node->text_len > 0 ? need_text(tree, node) : NULL;
    th_mo_char_data_changed(tree, node, old, node->text_len);
    if (len == 0) {
        node->text = NULL;
        node->text_len = 0;
        return 0;
    }
    Py_UCS4 *owned = arena_alloc(tree, len * (Py_ssize_t)sizeof(Py_UCS4));
    if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    memcpy(owned, data, (size_t)len * sizeof(Py_UCS4));
    node->text = owned;
    node->text_len = len;
    return 0;
}

/* Remove the attribute with this name, shifting the rest down. Returns 1 when one
   was removed, 0 when the element had no such attribute. */
Py_ssize_t th_node_attr_find(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len) {
    uint32_t atom = th_attr_lookup(tree, name, name_len);
    if (atom != UINT32_MAX) {
        for (Py_ssize_t index = 0; index < node->attr_count; index++) {
            if (node->attrs[index].name_atom == atom) {
                return index;
            }
        }
    }
    /* A foreign element can store a case-adjusted attribute name (definitionURL)
       whose atom differs from the lowercased probe, so match case-insensitively
       against the stored names; the probe is already lowercased by the caller. XML
       keeps names case-sensitive, so its exact-atom match above is the only pass. */
    if (!tree->xml && node->ns != TH_NS_HTML) {
        for (Py_ssize_t index = 0; index < node->attr_count; index++) {
            Py_ssize_t stored_len;
            const char *stored = th_attr_name(tree, node->attrs[index].name_atom, &stored_len);
            if (stored_len != name_len) {
                continue;
            }
            Py_ssize_t offset = 0;
            while (offset < name_len && lower_ascii((Py_UCS4)(unsigned char)stored[offset]) == (Py_UCS4)name[offset]) {
                offset++;
            }
            if (offset == name_len) {
                return index;
            }
        }
    }
    return -1;
}

int th_node_attr_del(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len) {
    tree->attr_version++;
    Py_ssize_t index = th_node_attr_find(tree, node, name, name_len);
    if (index < 0) {
        return 0;
    }
    mark_start_dirty(tree, node);
    th_mo_attr_changed(tree, node, node->attrs[index].name_atom, node->attrs[index].value, node->attrs[index].value_len,
                       1);
    tree->id_version += node->attrs[index].name_atom == TH_ATTR_ID;
    for (Py_ssize_t shift = index; shift + 1 < node->attr_count; shift++) {
        node->attrs[shift] = node->attrs[shift + 1];
    }
    node->attr_count--;
    return 1;
}

void th_node_remove(th_node *child) {
    node_remove(child);
}

void th_node_append_child(th_node *parent, th_node *child) {
    node_append(parent, child);
}

void th_node_insert_before(th_node *parent, th_node *child, th_node *ref) {
    node_insert_before(parent, child, ref);
}

/* The MutationObserver registry accessors dom/observe.c reaches the tree fields through. */
int th_tree_has_observers(const th_tree *tree) {
    return tree->observer_count > 0;
}

struct th_observer ***th_tree_observers_ptr(th_tree *tree) {
    return &tree->observers;
}

Py_ssize_t *th_tree_observer_count_ptr(th_tree *tree) {
    return &tree->observer_count;
}

Py_ssize_t *th_tree_observer_cap_ptr(th_tree *tree) {
    return &tree->observer_cap;
}

/* The observing counterparts the binding layer (dom/element.c) routes user mutations
   through: they relink exactly as above and queue a childList mutation record for the
   tree's observers. A removal reads the siblings while child is still linked, then
   detaches; an insertion links first, so child's fresh siblings are the record's. */
int th_tree_add_node_iterator(th_tree *tree, th_node_iterator *iterator) {
    if (tree->node_iterator_count == tree->node_iterator_cap) {
        Py_ssize_t cap = tree->node_iterator_cap == 0 ? 4 : tree->node_iterator_cap * 2;
        th_node_iterator **grown = PyMem_Realloc(tree->node_iterators, (size_t)cap * sizeof(th_node_iterator *));
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        tree->node_iterators = grown;
        tree->node_iterator_cap = cap;
    }
    tree->node_iterators[tree->node_iterator_count++] = iterator;
    return 0;
}

void th_tree_remove_node_iterator(th_tree *tree, th_node_iterator *iterator) {
    /* the iterator is always registered, so the scan always breaks and never runs to the end */
    for (Py_ssize_t index = 0; index < tree->node_iterator_count; index++) { /* GCOVR_EXCL_BR_LINE */
        if (tree->node_iterators[index] == iterator) {
            tree->node_iterators[index] = tree->node_iterators[--tree->node_iterator_count];
            break;
        }
    }
    if (tree->node_iterator_count == 0) {
        PyMem_Free(tree->node_iterators);
        tree->node_iterators = NULL;
        tree->node_iterator_cap = 0;
    }
}

static int is_inclusive_ancestor_of(const th_node *ancestor, const th_node *node) {
    for (const th_node *walk = node; walk != NULL; walk = walk->parent) {
        if (walk == ancestor) {
            return 1;
        }
    }
    return 0;
}

/* The DOM "adjust a node pointer" steps for removed, which is about to leave its parent: a pointer into the removed
   subtree moves to the first node after that subtree within root when it pointed before its node, else (or when no
   such node exists) to the node just before the subtree, pointing after it. */
static void adjust_node_pointer(const th_node_iterator *iterator, th_node *removed, th_node **node, int *before) {
    if (!is_inclusive_ancestor_of(removed, *node) || is_inclusive_ancestor_of(removed, iterator->root)) {
        return;
    }
    if (*before) {
        /* a node pointer can leave root through an unobserved edit (a Range extract), so the walk may miss root */
        for (th_node *walk = removed; walk != NULL && walk != iterator->root; walk = walk->parent) {
            if (walk->next_sibling != NULL) {
                *node = walk->next_sibling;
                return;
            }
        }
    }
    th_node *previous = removed->prev_sibling;
    if (previous == NULL) {
        *node = removed->parent;
    } else {
        while (previous->last_child != NULL) {
            previous = previous->last_child;
        }
        *node = previous;
    }
    *before = 0;
}

void th_node_remove_observed(th_tree *tree, th_node *child) {
    th_node *parent = child->parent;
    if (parent != NULL) {
        th_mo_child_removed(tree, parent, child, child->prev_sibling, child->next_sibling);
        for (Py_ssize_t index = 0; index < tree->node_iterator_count; index++) {
            th_node_iterator *iterator = tree->node_iterators[index];
            adjust_node_pointer(iterator, child, &iterator->reference, &iterator->reference_before);
            if (iterator->candidate != NULL) {
                adjust_node_pointer(iterator, child, &iterator->candidate, &iterator->candidate_before);
            }
        }
    }
    node_remove(child);
}

void th_node_append_child_observed(th_tree *tree, th_node *parent, th_node *child) {
    node_append(parent, child);
    th_mo_child_inserted(tree, parent, child);
}

/* Whether node sits in the sibling run [first, last] (never when first is NULL). */
static int in_run(const th_node *node, const th_node *first, const th_node *last) {
    if (first == NULL) {
        return 0;
    }
    for (const th_node *walk = first;; walk = walk->next_sibling) {
        if (walk == node) {
            return 1;
        }
        if (walk == last) {
            return 0;
        }
    }
}

/* Whether a child of type sits between from (inclusive) and until (exclusive; NULL runs to the end), skipping the
   replaced run. */
static int has_child_between(th_node *from, const th_node *until, enum th_node_type type, const th_node *run_first,
                             const th_node *run_last) {
    for (th_node *walk = from; walk != until; walk = walk->next_sibling) {
        if (walk->type == type && !in_run(walk, run_first, run_last)) {
            return 1;
        }
    }
    return 0;
}

const char *th_pre_insert_error(th_node *parent, th_node *const *nodes, Py_ssize_t count, th_node *child,
                                th_node *run_first, th_node *run_last) {
    Py_ssize_t elements = 0;
    Py_ssize_t doctypes = 0;
    Py_ssize_t texts = 0;
    for (Py_ssize_t index = 0; index < count; index++) {
        elements += nodes[index]->type == TH_NODE_ELEMENT;
        doctypes += nodes[index]->type == TH_NODE_DOCTYPE;
        texts += nodes[index]->type == TH_NODE_TEXT || nodes[index]->type == TH_NODE_CDATA;
    }
    /* several nodes are gathered into a fragment first, and a fragment cannot hold a doctype */
    if (doctypes > 0 && (parent->type != TH_NODE_DOCUMENT || count > 1)) {
        return "a doctype can only be a child of a Document";
    }
    if (parent->type != TH_NODE_DOCUMENT) {
        return NULL;
    }
    if (texts > 0) {
        return "a Document cannot hold a Text node";
    }
    if (elements > 1) {
        return "a Document can hold only one element";
    }
    /* a replacement goes where the replaced run starts; an insertion before child */
    th_node *after = run_first != NULL ? run_last->next_sibling : child;
    th_node *before = run_first != NULL ? run_first : child;
    if (elements == 1) {
        if (has_child_between(parent->first_child, NULL, TH_NODE_ELEMENT, run_first, run_last)) {
            return "a Document can hold only one element";
        }
        if (has_child_between(after, NULL, TH_NODE_DOCTYPE, run_first, run_last)) {
            return "a Document's element must come after its doctype";
        }
    }
    if (doctypes == 1) {
        if (has_child_between(parent->first_child, NULL, TH_NODE_DOCTYPE, run_first, run_last)) {
            return "a Document can hold only one doctype";
        }
        if (has_child_between(parent->first_child, before, TH_NODE_ELEMENT, run_first, run_last)) {
            return "a Document's doctype must come before its element";
        }
    }
    return NULL;
}

void th_node_insert_before_observed(th_tree *tree, th_node *parent, th_node *child, th_node *ref) {
    node_insert_before(parent, child, ref);
    th_mo_child_inserted(tree, parent, child);
}

/* Whether ancestor is a host-including inclusive ancestor of node (DOM pre-insert), the test that rejects making a
   node a descendant of itself. The walk crosses from a shadow root to its host, so a host cannot move into its own
   shadow tree. */
int th_node_contains(th_tree *tree, th_node *ancestor, th_node *node) {
    for (th_node *walk = node; walk != NULL;
         walk = walk->parent == NULL && th_node_is_shadow_root(walk) ? th_shadow_host(tree, walk) : walk->parent) {
        if (walk == ancestor) {
            return 1;
        }
    }
    return 0;
}

/* Whether two attributes carry the same name. Names resolve to their interned
   bytes, so a per-tree dynamic atom in one tree matches the same spelling in the
   other (the numeric atoms differ across trees). */
static int attr_name_equal(th_tree *left_tree, const th_node_attr *left, th_tree *right_tree,
                           const th_node_attr *right) {
    Py_ssize_t left_len, right_len;
    const char *left_name = th_attr_name(left_tree, left->name_atom, &left_len);
    const char *right_name = th_attr_name(right_tree, right->name_atom, &right_len);
    return left_len == right_len && memcmp(left_name, right_name, (size_t)left_len) == 0;
}

/* Whether two attributes carry the same value. A valueless attribute (NULL value,
   zero length) is the empty string per the DOM, so `disabled` equals `disabled=""`. */
static int attr_value_equal(const th_node_attr *left, const th_node_attr *right) {
    return left->value_len == right->value_len &&
           (left->value_len == 0 || memcmp(left->value, right->value, (size_t)left->value_len * sizeof(Py_UCS4)) == 0);
}

static int attrs_equal_indexed(th_tree *left_tree, th_node *left, th_tree *right_tree, th_node *right,
                               Py_ssize_t start);

static int attrs_equal(th_tree *left_tree, th_node *left, th_tree *right_tree, th_node *right) {
    if (left->attr_count != right->attr_count) {
        return 0;
    }
    int can_index = left->attr_count >= 32 &&
                    /* GCOVR_EXCL_BR_START: allocation size overflow */
                    (size_t)left->attr_count <= SIZE_MAX / (4 * sizeof(Py_ssize_t));
    /* GCOVR_EXCL_BR_STOP */
    Py_ssize_t comparisons = 0;
    for (Py_ssize_t index = 0; index < left->attr_count; index++) {
        const th_node_attr *want = &left->attrs[index];
        Py_ssize_t other = 0;
        for (; other < right->attr_count; other++) {
            if (attr_name_equal(left_tree, want, right_tree, &right->attrs[other])) {
                if (!attr_value_equal(want, &right->attrs[other])) {
                    return 0;
                }
                break;
            }
        }
        if (other == right->attr_count) {
            return 0;
        }
        if (can_index) {
            comparisons += other + 1;
            if (comparisons >= left->attr_count * 2) {
                can_index = 0;
                const int indexed = attrs_equal_indexed(left_tree, left, right_tree, right, index + 1);
                if (indexed >= 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    return indexed;
                }
            } /* GCOVR_EXCL_LINE: allocation failure fallback */
        }
    }
    return 1;
}

static int attrs_equal_indexed(th_tree *left_tree, th_node *left, th_tree *right_tree, th_node *right,
                               Py_ssize_t start) {
    size_t capacity = 64;
    while (capacity < (size_t)right->attr_count * 2) {
        capacity *= 2;
    }
    Py_ssize_t *slots = PyMem_Calloc(capacity, sizeof(Py_ssize_t));
    if (slots == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: fall back to the allocation-free comparison */
    }
    for (Py_ssize_t index = 0; index < right->attr_count; index++) {
        /* an element never carries one name twice, so every name takes a fresh slot */
        size_t slot = ((size_t)right->attrs[index].name_atom * 2654435761U) & (capacity - 1);
        while (slots[slot] != 0) {
            slot = (slot + 1) & (capacity - 1);
        }
        slots[slot] = index + 1;
    }
    int equal = 1;
    for (Py_ssize_t index = start; index < left->attr_count; index++) {
        const th_node_attr *want = &left->attrs[index];
        uint32_t atom = want->name_atom;
        if (left_tree != right_tree) {
            Py_ssize_t name_len;
            const char *name = th_attr_name(left_tree, atom, &name_len);
            atom = th_attr_lookup(right_tree, name, name_len);
        }
        size_t slot = ((size_t)atom * 2654435761U) & (capacity - 1);
        while (slots[slot] != 0 && right->attrs[slots[slot] - 1].name_atom != atom) {
            slot = (slot + 1) & (capacity - 1);
        }
        if (slots[slot] == 0 || !attr_value_equal(want, &right->attrs[slots[slot] - 1])) {
            equal = 0;
            break;
        }
    }
    PyMem_Free(slots);
    return equal;
}

/* Whether two nodes' own character data match, realizing a borrowed text span first. */
static int data_equal(th_tree *left_tree, th_node *left, th_tree *right_tree, th_node *right) {
    if (left->text_len != right->text_len) {
        return 0;
    }
    const Py_UCS4 *left_text = need_text(left_tree, left);
    const Py_UCS4 *right_text = need_text(right_tree, right);
    return left->text_len == 0 || memcmp(left_text, right_text, (size_t)left->text_len * sizeof(Py_UCS4)) == 0;
}

static int node_data_equals(th_tree *left_tree, th_node *left, th_tree *right_tree, th_node *right) {
    if (left->type != right->type) {
        return 0;
    }
    switch ((enum th_node_type)left->type) { /* GCOVR_EXCL_BR_LINE: node types are exhaustive */
    case TH_NODE_ELEMENT:
        if (left->ns != right->ns) {
            return 0;
        }
        if (!data_equal(left_tree, left, right_tree, right)) {
            return 0;
        }
        if (!attrs_equal(left_tree, left, right_tree, right)) {
            return 0;
        }
        break;
    case TH_NODE_DOCTYPE:
        /* tag_flags records whether the source supplied a public/system id, which the
           id text alone cannot express (a missing and an empty id both serialize empty). */
        if (left->tag_flags != right->tag_flags) {
            return 0;
        }
        if (!data_equal(left_tree, left, right_tree, right)) {
            return 0;
        }
        break;
    case TH_NODE_PI:
        /* attr_count holds the packed target/data split point. */
        if (left->attr_count != right->attr_count) {
            return 0;
        }
        if (!data_equal(left_tree, left, right_tree, right)) {
            return 0;
        }
        break;
    case TH_NODE_TEXT:
    case TH_NODE_COMMENT:
    case TH_NODE_CDATA:
        if (!data_equal(left_tree, left, right_tree, right)) {
            return 0;
        }
        break;
    case TH_NODE_DOCUMENT:
    case TH_NODE_CONTENT:
        break; /* a document / template-content fragment compares purely by its children */
    }
    return 1;
}

/* Node.equals compares the two shapes in lockstep, using parent links to return from a subtree instead of the C stack.
 */
int th_node_equals(th_tree *left_tree, th_node *left, th_tree *right_tree, th_node *right) {
    th_node *left_root = left;
    th_node *right_root = right;
    for (;;) {
        if (!node_data_equals(left_tree, left, right_tree, right)) {
            return 0;
        }
        if ((left->first_child == NULL) != (right->first_child == NULL)) {
            return 0;
        }
        if (left->first_child != NULL) {
            left = left->first_child;
            right = right->first_child;
            continue;
        }
        while (left != left_root && left->next_sibling == NULL) {
            if (right->next_sibling != NULL) {
                return 0;
            }
            left = left->parent;
            right = right->parent;
        }
        if (left == left_root) {
            return right == right_root;
        }
        if (right->next_sibling == NULL) {
            return 0;
        }
        left = left->next_sibling;
        right = right->next_sibling;
    }
}

/* The ASCII-lowercased atom for an attribute name stored in tree, re-interned when folding changes it. Returns
   TH_ATTR_UNKNOWN on allocation failure. */
static uint32_t fold_attr_atom(th_tree *tree, uint32_t atom) {
    Py_ssize_t name_len;
    const char *name = th_attr_name(tree, atom, &name_len);
    Py_ssize_t first_upper = 0;
    while (first_upper < name_len && !(name[first_upper] >= 'A' && name[first_upper] <= 'Z')) {
        first_upper++;
    }
    if (first_upper == name_len) {
        return atom;
    }
    char *folded = PyMem_Malloc((size_t)name_len);
    if (folded == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return TH_ATTR_UNKNOWN; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < name_len; index++) {
        char ch = name[index];
        folded[index] = ch >= 'A' && ch <= 'Z' ? (char)(ch + 32) : ch;
    }
    uint32_t folded_atom = th_attr_intern_utf8(tree, folded, name_len);
    PyMem_Free(folded);
    return folded_atom;
}

/* Rewrite a copied element for a tree of the other kind. An XML tree keeps every element as an unknown atom under its
   spelled name, so an HTML element moving in drops the atom that made it raw text (a <script> would otherwise
   serialize "a<b" unescaped). An HTML tree stores and matches HTML element and attribute names in ASCII lowercase, and
   an XML tree's elements all carry the HTML namespace, so an XML element moving in is folded, keeping the first of two
   attributes that fold to one name, as the HTML tokenizer does. Returns 0, or -1 on allocation failure. */
static int convert_element_kind(th_tree *dest, th_node *node) {
    if (dest->xml) {
        node->atom = TH_TAG_UNKNOWN;
        node->tag_flags = th_tag_flags(TH_TAG_UNKNOWN);
        return 0;
    }
    char ascii[64];
    int is_ascii = node->text_len <= (Py_ssize_t)sizeof(ascii);
    for (Py_ssize_t index = 0; index < node->text_len; index++) {
        if (node->text[index] >= 'A' && node->text[index] <= 'Z') {
            node->text[index] += 32;
        }
        if (node->text[index] >= 0x80) {
            is_ascii = 0;
        } else if (is_ascii) {
            ascii[index] = (char)node->text[index];
        }
    }
    node->atom = is_ascii ? th_tag_lookup(ascii, node->text_len) : TH_TAG_UNKNOWN;
    node->tag_flags = th_tag_flags(node->atom);
    Py_ssize_t kept = 0;
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        uint32_t atom = fold_attr_atom(dest, node->attrs[index].name_atom);
        if (atom == TH_ATTR_UNKNOWN) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;                 /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_ssize_t earlier = 0;
        while (earlier < kept && node->attrs[earlier].name_atom != atom) {
            earlier++;
        }
        if (earlier == kept) {
            node->attrs[kept] = node->attrs[index];
            node->attrs[kept++].name_atom = atom;
        }
    }
    node->attr_count = kept;
    return 0;
}

/* Copy one node without its children, materializing borrowed text and re-interning per-tree attribute atoms. */
th_node *th_tree_copy_node_shallow(th_tree *dest, th_tree *src, th_node *src_node) {
    th_node *node = node_new(dest, src_node->type);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    node->atom = src_node->atom;
    node->ns = src_node->ns;
    node->tag_flags = src_node->tag_flags;
    if (src_node->text_len > 0) {
        const Py_UCS4 *text = need_text(src, src_node);
        Py_UCS4 *owned = arena_alloc(dest, src_node->text_len * (Py_ssize_t)sizeof(Py_UCS4));
        if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        memcpy(owned, text, (size_t)src_node->text_len * sizeof(Py_UCS4));
        node->text = owned;
        node->text_len = src_node->text_len;
    }
    if (src_node->type == TH_NODE_PI || src_node->type == TH_NODE_DOCTYPE) {
        node->attr_count = src_node->attr_count; /* PI target/data or doctype public-id split point */
    }
    if (src_node->type == TH_NODE_ELEMENT && src_node->attr_count > 0) {
        node->attrs = arena_alloc(dest, src_node->attr_count * (Py_ssize_t)sizeof(th_node_attr));
        if (node->attrs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        memset(node->attrs, 0, (size_t)src_node->attr_count * sizeof(th_node_attr));
        node->attr_count = src_node->attr_count;
        for (Py_ssize_t index = 0; index < src_node->attr_count; index++) {
            const th_node_attr *from = &src_node->attrs[index];
            uint32_t atom = from->name_atom;
            if (atom >= TH_ATTR__DYNAMIC_BASE) {
                Py_ssize_t name_len;
                const char *name = th_attr_name(src, atom, &name_len);
                atom = th_attr_intern_utf8(dest, name, name_len);
            }
            node->attrs[index].name_atom = atom;
            if (from->value != NULL) {
                Py_UCS4 *value =
                    arena_alloc(dest, (from->value_len ? from->value_len : 1) * (Py_ssize_t)sizeof(Py_UCS4));
                if (value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                    return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
                }
                memcpy(value, from->value, (size_t)from->value_len * sizeof(Py_UCS4));
                node->attrs[index].value = value;
                node->attrs[index].value_len = from->value_len;
            }
        }
    }
    return node;
}

static th_node *copy_node_iterative(th_tree *dest, th_tree *src, th_node *src_node) {
    th_node *root = th_tree_copy_node_shallow(dest, src, src_node);
    if (root == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node *from = src_node;
    th_node *copy = root;
    for (;;) {
        if (from->first_child != NULL) {
            from = from->first_child;
            th_node *child = th_tree_copy_node_shallow(dest, src, from);
            if (child == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            node_append(copy, child);
            copy = child;
            continue;
        }
        while (from != src_node && from->next_sibling == NULL) {
            from = from->parent;
            copy = copy->parent;
        }
        if (from == src_node) {
            return root;
        }
        from = from->next_sibling;
        th_node *sibling = th_tree_copy_node_shallow(dest, src, from);
        if (sibling == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        node_append(copy->parent, sibling);
        copy = sibling;
    }
}

#define TH_COPY_RECURSION_LIMIT 64

static th_node *copy_node_at(th_tree *dest, th_tree *src, th_node *src_node, int depth) {
    if (depth == TH_COPY_RECURSION_LIMIT) {
        return copy_node_iterative(dest, src, src_node);
    }
    th_node *node = th_tree_copy_node_shallow(dest, src, src_node);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (th_node *child = src_node->first_child; child != NULL; child = child->next_sibling) {
        th_node *copy = copy_node_at(dest, src, child, depth + 1);
        if (copy == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        node_append(node, copy);
    }
    return node;
}

th_node *th_tree_copy_node(th_tree *dest, th_tree *src, th_node *src_node) {
    return copy_node_at(dest, src, src_node, 0);
}

int th_tree_has_shadows(const th_tree *tree) {
    return tree->shadow_count != 0;
}

th_node *th_node_next_including_shadow(th_tree *tree, th_node *node, th_node *root) {
    th_node *shadow = th_element_shadow_root(tree, node);
    if (shadow != NULL) {
        return shadow;
    }
    if (node->first_child != NULL) {
        return node->first_child;
    }
    while (node != root) {
        if (th_node_is_shadow_root(node)) {
            node = th_shadow_host(tree, node);
            if (node->first_child != NULL) {
                return node->first_child;
            }
        } else {
            if (node->next_sibling != NULL) {
                return node->next_sibling;
            }
            node = node->parent;
        }
    }
    return NULL;
}

static th_node *copy_adopted_shadows(th_tree *dest, th_tree *src, th_node *src_node) {
    th_node *root = th_tree_copy_node(dest, src, src_node);
    if (root == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    th_node *from = src_node;
    th_node *copy = root;
    do {
        th_node *shadow = th_element_shadow_root(src, from);
        if (shadow != NULL) {
            th_node *adopted = th_element_attach_shadow(dest, copy, th_shadow_mode(shadow));
            if (adopted == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return NULL;       /* GCOVR_EXCL_LINE */
            }
            adopted->tag_flags = shadow->tag_flags;
            for (th_node *child = shadow->first_child; child != NULL; child = child->next_sibling) {
                th_node *child_copy = th_tree_copy_node(dest, src, child);
                if (child_copy == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    return NULL;          /* GCOVR_EXCL_LINE */
                }
                node_append(adopted, child_copy);
            }
        }
        from = th_node_next_including_shadow(src, from, src_node);
        copy = th_node_next_including_shadow(dest, copy, root);
    } while (from != NULL);
    return root;
}

th_node *th_tree_adopt_copy(th_tree *dest, th_tree *src, th_node *src_node) {
    th_node *copy =
        src->shadow_count == 0 ? th_tree_copy_node(dest, src, src_node) : copy_adopted_shadows(dest, src, src_node);
    if (copy == NULL || src->xml == dest->xml) { /* GCOVR_EXCL_BR_LINE: the copy is NULL on OOM only */
        return copy;
    }
    for (th_node *node = copy; node != NULL; node = th_node_next_including_shadow(dest, node, copy)) {
        if (node->type == TH_NODE_ELEMENT && convert_element_kind(dest, node) < 0) { /* GCOVR_EXCL_BR_LINE: OOM only */
            return NULL; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return copy;
}

th_tree *th_tree_new_rooted(enum th_node_type type, int xml, int quirks) {
    th_tree *tree = th_tree_new();
    if (tree == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    tree->xml = xml;
    tree->quirks = quirks;
    tree->document = node_new(tree, type);
    if (tree->document == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);       /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return tree;
}

/* Copy a document into an independent tree while its caller holds the source-tree lock. */
th_tree *th_tree_copy_document(th_tree *src) {
    th_tree *dest = th_tree_new();
    if (dest == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    dest->xml = src->xml;
    dest->quirks = src->quirks;
    dest->document = th_tree_copy_node(dest, src, src->document);
    if (dest->document == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(dest);       /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;              /* GCOVR_EXCL_LINE */
    }
    return dest;
}

static void normalize_children(th_tree *tree, th_node *root) {
    for (th_node *child = root->first_child; child != NULL;) {
        th_node *next = child->next_sibling;
        if (child->type == TH_NODE_TEXT) {
            if (child->text_len == 0) {
                th_node_remove(child);
                child = next;
                continue;
            }
            th_node *end = next;
            Py_ssize_t merged_len = child->text_len;
            while (end != NULL && end->type == TH_NODE_TEXT) {
                th_node *after = end->next_sibling;
                if (end->text_len == 0) {
                    th_node_remove(end);
                } else {
                    merged_len += end->text_len;
                }
                end = after;
            }
            next = child->next_sibling;
            if (merged_len > child->text_len) {
                Py_UCS4 *merged = arena_alloc(tree, merged_len * (Py_ssize_t)sizeof(Py_UCS4));
                if (merged == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                    return;           /* GCOVR_EXCL_LINE: allocation-failure path */
                }
                Py_ssize_t offset = 0;
                for (th_node *part = child; part != end; part = part->next_sibling) {
                    const Py_UCS4 *text = need_text(tree, part);
                    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: text realization fails only on allocation failure */
                        return;         /* GCOVR_EXCL_LINE: allocation-failure path */
                    }
                    memcpy(merged + offset, text, (size_t)part->text_len * sizeof(Py_UCS4));
                    offset += part->text_len;
                }
                child->text = merged;
                child->text_len = merged_len;
            }
            while (next != end) {
                th_node *after = next->next_sibling;
                th_node_remove(next);
                next = after;
            }
        }
        child = next;
    }
}

void th_node_normalize(th_tree *tree, th_node *root) {
    th_node *node = root;
    for (;;) {
        if (node == root || node->type == TH_NODE_ELEMENT) {
            normalize_children(tree, node);
        }
        if (node->first_child != NULL) {
            node = node->first_child;
            continue;
        }
        while (node != root && node->next_sibling == NULL) {
            node = node->parent;
        }
        if (node == root) {
            return;
        }
        node = node->next_sibling;
    }
}

/* Construct one shell element (html/head/body/meta/title) from its ASCII tag name,
   with no attribute slots. Every shell name fits the small stack buffer, so the
   UCS4 widening never overflows. NULL on allocation failure. */
static th_node *shell_element(th_tree *tree, const char *name, Py_ssize_t len, uint16_t atom) {
    Py_UCS4 tag[8]; /* the longest shell tag is "title" (5 code points) */
    for (Py_ssize_t index = 0; index < len; index++) {
        tag[index] = (Py_UCS4)name[index];
    }
    return th_tree_make_element(tree, tag, len, atom, 0);
}

th_node *th_tree_build_shell(th_tree *tree, const Py_UCS4 *lang, Py_ssize_t lang_len, const Py_UCS4 *title,
                             Py_ssize_t title_len, const Py_UCS4 *charset, Py_ssize_t charset_len, th_node **out_head,
                             th_node **out_body) {
    static const Py_UCS4 doctype_html[] = {'h', 't', 'm', 'l'};
    th_node *document = node_new(tree, TH_NODE_DOCUMENT);
    th_node *doctype = th_tree_make_data_node(tree, TH_NODE_DOCTYPE, doctype_html, 4);
    th_node *html = shell_element(tree, "html", 4, TH_TAG_HTML);
    th_node *head = shell_element(tree, "head", 4, TH_TAG_HEAD);
    th_node *body = shell_element(tree, "body", 4, TH_TAG_BODY);
    /* allocation failure cannot be forced from a test */
    if (document == NULL || doctype == NULL || html == NULL || head == NULL || body == NULL) { /* GCOVR_EXCL_BR_LINE */
        return NULL; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    tree->document = document;
    node_append(document, doctype);
    node_append(document, html);
    node_append(html, head);
    node_append(html, body);
    if (lang != NULL && th_node_attr_set(tree, html, "lang", 4, lang, lang_len, 1) < 0) { /* GCOVR_EXCL_BR_LINE: OOM */
        return NULL; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (charset != NULL) {
        th_node *meta = shell_element(tree, "meta", 4, TH_TAG_META);
        if (meta == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (th_node_attr_set(tree, meta, "charset", 7, charset, charset_len, 1) < 0) { /* GCOVR_EXCL_BR_LINE: OOM */
            return NULL; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        node_append(head, meta);
    }
    if (title != NULL) {
        th_node *title_el = shell_element(tree, "title", 5, TH_TAG_TITLE);
        if (title_el == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        node_append(head, title_el);
        if (title_len > 0) {
            th_node *text = th_tree_make_data_node(tree, TH_NODE_TEXT, title, title_len);
            if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            node_append(title_el, text);
        }
    }
    *out_head = head;
    *out_body = body;
    return document;
}
