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

#include "core/ascii.h" /* lower_ascii for the foreign case-insensitive attribute scan */

#include <string.h>

/* An empty tree for programmatically constructed nodes: the arena grows on the
   first allocation and can_span stays 0, so a node's text is always owned rather
   than a borrowed span. */
th_tree *th_tree_new(void) {
    return PyMem_Calloc(1, sizeof(th_tree));
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

/* Construct an element node owning a copy of the tag name, with attr_count empty
   attribute slots to fill with th_tree_set_attr. */
th_node *th_tree_make_element(th_tree *tree, const Py_UCS4 *tag, Py_ssize_t tag_len, uint16_t atom,
                              Py_ssize_t attr_count) {
    th_node *node = node_new(tree, TH_NODE_ELEMENT);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    node->atom = atom;
    node->tag_flags = th_tag_flags(atom); /* so a constructed/unpickled raw-text element serializes literally */
    Py_UCS4 *owned = arena_alloc(tree, tag_len * (Py_ssize_t)sizeof(Py_UCS4));
    if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    memcpy(owned, tag, (size_t)tag_len * sizeof(Py_UCS4));
    node->text = owned;
    node->text_len = tag_len;
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

/* Upsert an attribute by name: replace the value of the existing attribute with
   that atom, or append a new slot (growing the arena array by one). has_value 0
   stores a valueless attribute; an empty value stays distinct from valueless.
   Returns 0, or -1 on allocation failure. */
int th_node_attr_set(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len, const Py_UCS4 *value,
                     Py_ssize_t value_len, int has_value) {
    uint32_t atom = th_attr_intern_utf8(tree, name, name_len);
    Py_UCS4 *owned = NULL;
    if (has_value) {
        owned = arena_alloc(tree, (value_len ? value_len : 1) * (Py_ssize_t)sizeof(Py_UCS4));
        if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        memcpy(owned, value, (size_t)value_len * sizeof(Py_UCS4));
    }
    Py_ssize_t existing = th_node_attr_find(tree, node, name, name_len);
    if (existing >= 0) {
        node->attrs[existing].value = owned;
        node->attrs[existing].value_len = has_value ? value_len : 0;
        return 0;
    }
    th_node_attr *grown = arena_alloc(tree, (node->attr_count + 1) * (Py_ssize_t)sizeof(th_node_attr));
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    memcpy(grown, node->attrs, (size_t)node->attr_count * sizeof(th_node_attr));
    grown[node->attr_count].name_atom = atom;
    grown[node->attr_count].value = owned;
    grown[node->attr_count].value_len = has_value ? value_len : 0;
    node->attrs = grown;
    node->attr_count++;
    return 0;
}

/* Replace a node's character data with a copy of len code points (an empty buffer
   is stored as none). Returns 0, or -1 on allocation failure. */
int th_node_set_data(th_tree *tree, th_node *node, const Py_UCS4 *data, Py_ssize_t len) {
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
       against the stored names; the probe is already lowercased by the caller. */
    if (node->ns != TH_NS_HTML) {
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
    Py_ssize_t index = th_node_attr_find(tree, node, name, name_len);
    if (index < 0) {
        return 0;
    }
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

/* Whether ancestor is node itself or one of its ancestors, the test that rejects
   making a node a descendant of itself. */
int th_node_contains(th_node *ancestor, th_node *node) {
    for (th_node *walk = node; walk != NULL; walk = walk->parent) {
        if (walk == ancestor) {
            return 1;
        }
    }
    return 0;
}

/* Deep-copy a node and its subtree from src into dest's arena, materializing
   borrowed text and re-interning per-tree attribute atoms. Used to adopt a node
   from another tree without retaining the source. NULL on allocation failure. */
th_node *th_tree_copy_node(th_tree *dest, th_tree *src, th_node *src_node) {
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
    if (src_node->type == TH_NODE_PI) {
        node->attr_count = src_node->attr_count; /* the packed target/data split point */
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
    for (th_node *child = src_node->first_child; child != NULL; child = child->next_sibling) {
        th_node *copy = th_tree_copy_node(dest, src, child);
        if (copy == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        node_append(node, copy);
    }
    return node;
}

/* DOM normalize: merge adjacent Text children into the first of each run and drop
   empty Text nodes, recursing into every element. Merged runs get a fresh arena
   buffer; on allocation failure the merge stops early, leaving a valid tree. */
void th_node_normalize(th_tree *tree, th_node *root) {
    for (th_node *child = root->first_child; child != NULL;) {
        th_node *next = child->next_sibling;
        if (child->type == TH_NODE_ELEMENT) {
            th_node_normalize(tree, child);
        } else if (child->type == TH_NODE_TEXT) {
            if (child->text_len == 0) {
                th_node_remove(child);
                child = next;
                continue;
            }
            while (next != NULL && next->type == TH_NODE_TEXT) {
                th_node *after = next->next_sibling;
                if (next->text_len > 0) {
                    Py_ssize_t merged_len = child->text_len + next->text_len;
                    Py_UCS4 *merged = arena_alloc(tree, merged_len * (Py_ssize_t)sizeof(Py_UCS4));
                    if (merged == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                        return;           /* GCOVR_EXCL_LINE: allocation-failure path */
                    }
                    memcpy(merged, need_text(tree, child), (size_t)child->text_len * sizeof(Py_UCS4));
                    memcpy(merged + child->text_len, need_text(tree, next), (size_t)next->text_len * sizeof(Py_UCS4));
                    child->text = merged;
                    child->text_len = merged_len;
                }
                th_node_remove(next);
                next = after;
            }
        }
        child = next;
    }
}
