/* Evaluates a compiled program against a tree: walks the axes, filters by predicate,
   converts between the four XPath value types, and compares and combines results. */

#include "core/common.h"
#include "core/vec.h"
#include "dom/tree.h"
#include "query/xpath/internal.h"
#include "query/xpath/regex.h"
#include "query/xpath/xpath.h"

#include <math.h>
#include <string.h>

typedef struct {
    th_node_attr attribute;
    size_t references;
    Py_ssize_t ordinal;
    Py_ssize_t name_len;
    char name[];
} xp_attribute;

static xp_attribute *attribute_snapshot(Py_ssize_t attr) {
    uintptr_t address = (uintptr_t)attr & ~(uintptr_t)1;
    xp_attribute *snapshot;
    memcpy(&snapshot, &address, sizeof(xp_attribute *));
    return snapshot;
}

static xp_attribute *item_snapshot(xp_item item) {
    return xp_is_attribute(item.attr) && ((uintptr_t)item.attr & 1) ? attribute_snapshot(item.attr) : NULL;
}

const th_node_attr *xp_item_attribute(struct th_tree *tree, xp_item item) {
    (void)tree;
    return (uintptr_t)item.attr & 1 ? &attribute_snapshot(item.attr)->attribute : &item.node->attrs[item.attr >> 1];
}

const char *xp_item_attr_name(struct th_tree *tree, xp_item item, Py_ssize_t *len) {
    if ((uintptr_t)item.attr & 1) {
        xp_attribute *snapshot = attribute_snapshot(item.attr);
        *len = snapshot->name_len;
        return snapshot->name;
    }
    return th_attr_name(tree, item.node->attrs[item.attr >> 1].name_atom, len);
}

int xp_item_equal(xp_item left, xp_item right) {
    if (left.node != right.node) {
        return 0;
    }
    if (!xp_is_attribute(left.attr) || !xp_is_attribute(right.attr) ||
        !(((uintptr_t)left.attr | (uintptr_t)right.attr) & 1)) {
        return left.attr == right.attr;
    }
    return xp_item_attribute(NULL, left)->name_atom == xp_item_attribute(NULL, right)->name_atom;
}

uintptr_t xp_item_hash(xp_item item) {
    uintptr_t address = (uintptr_t)item.node;
    uintptr_t attr = xp_is_attribute(item.attr) ? xp_item_attribute(NULL, item)->name_atom : (uintptr_t)item.attr;
    return (address >> 4) ^ (address >> 13) ^ attr;
}

static void item_release(xp_item item) {
    xp_attribute *snapshot = item_snapshot(item);
    if (snapshot != NULL && --snapshot->references == 0) {
        PyMem_Free(snapshot);
    }
}

static int visit_set(const xp_nodeset *set, int (*visitor)(void *, struct th_node *), void *data) {
    for (Py_ssize_t index = 0; index < set->len; index++) {
        if (set->items[index].node != NULL) {
            if (visitor(data, set->items[index].node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                                   /* GCOVR_EXCL_LINE */
            }
        }
    }
    return 0;
}

const xp_live_frame *xp_live_previous(const xp_live_frame *frame) {
    return frame->previous;
}

uint64_t xp_live_id(const xp_live_frame *frame) {
    return frame->id;
}

uint64_t xp_live_version(const xp_live_frame *frame) {
    return frame->version;
}

Py_ssize_t xp_live_append_count(const xp_live_frame *frame) {
    return frame->append != NULL ? frame->append->len : 0;
}

int xp_visit_frame_append(const xp_live_frame *frame, Py_ssize_t start, int (*visitor)(void *, struct th_node *),
                          void *data) {
    for (Py_ssize_t index = start; index < xp_live_append_count(frame); index++) {
        if (visitor(data, frame->append->items[index].node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                             /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

int xp_visit_frame_nodes(const xp_live_frame *frame, int (*visitor)(void *, struct th_node *), void *data) {
    if (frame->node != NULL) {
        if (visitor(data, frame->node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                        /* GCOVR_EXCL_LINE */
        }
    }
    for (int index = 0; index < 2; index++) {
        if (frame->sets[index] != NULL) {
            if (visit_set(frame->sets[index], visitor, data) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                                          /* GCOVR_EXCL_LINE */
            }
        }
    }
    for (Py_ssize_t index = 0; index < frame->result_count; index++) {
        if (frame->results[index].kind == XP_NODESET) {
            int rc = visit_set(&frame->results[index].nodes, visitor, data);
            if (rc < 0) {  /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1; /* GCOVR_EXCL_LINE */
            }
        }
    }
    for (Py_ssize_t index = 0; frame->vars != NULL && index < frame->vars->len; index++) {
        const xp_result *value = &frame->vars->items[index].value;
        if (value->kind == XP_NODESET) {
            if (visit_set(&value->nodes, visitor, data) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                                     /* GCOVR_EXCL_LINE */
            }
        }
    }
    return xp_visit_frame_append(frame, 0, visitor, data);
}

#if defined(_MSC_VER)
#define TH_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define TH_NOINLINE __attribute__((noinline))
#else
#define TH_NOINLINE
#endif

/* Growth stays out of line so ordinary result appends can inline. */
static TH_NOINLINE int ns_grow(xp_nodeset *ns) {
    size_t cap;
    size_t bytes;
    int grew = th_grow_cap((size_t)ns->cap + 1, (size_t)ns->cap, 8, sizeof(xp_item), &cap, &bytes);
    if (!grew) {   /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
        return -1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
    }
    xp_item *grown = PyMem_Realloc(ns->items, bytes);
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    ns->items = grown;
    ns->cap = (Py_ssize_t)cap;
    return 0;
}

static inline int ns_append(xp_nodeset *ns, struct th_node *node, Py_ssize_t attr) {
    if (ns->len == ns->cap) {
        if (ns_grow(ns) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;         /* GCOVR_EXCL_LINE */
        }
    }
    ns->items[ns->len++] = (xp_item){node, attr};
    return 0;
}

int ns_push(xp_nodeset *ns, struct th_node *node, Py_ssize_t attr) {
    if (ns_append(ns, node, attr) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                       /* GCOVR_EXCL_LINE */
    }
    xp_attribute *snapshot = item_snapshot((xp_item){node, attr});
    if (snapshot != NULL) {
        snapshot->references++;
        ns->snapshots = 1;
    }
    return 0;
}

static TH_NOINLINE void release_snapshots(const xp_nodeset *ns) {
    for (Py_ssize_t index = 0; index < ns->len; index++) {
        item_release(ns->items[index]);
    }
}

void xp_nodeset_free(xp_nodeset *ns) {
    if (ns->snapshots) {
        release_snapshots(ns);
    }
    ns->snapshots = 0;
    PyMem_Free(ns->items);
    ns->items = NULL;
    ns->len = ns->cap = 0;
}

static int push_attribute(xp_nodeset *set, xp_ctx *ctx, struct th_node *node, Py_ssize_t index) {
    if (ctx->live == NULL) {
        return ns_append(set, node, index << 1);
    }
    Py_ssize_t name_len;
    const char *name = th_attr_name(ctx->tree, node->attrs[index].name_atom, &name_len);
    xp_attribute *snapshot = PyMem_Malloc(sizeof(*snapshot) + (size_t)name_len);
    if (snapshot == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;          /* GCOVR_EXCL_LINE */
    }
    snapshot->attribute = node->attrs[index];
    snapshot->references = 1;
    snapshot->ordinal = index;
    snapshot->name_len = name_len;
    memcpy(snapshot->name, name, (size_t)name_len);
    xp_item item = {node, (Py_ssize_t)((uintptr_t)snapshot | 1)};
    int rc = ns_push(set, node, item.attr);
    item_release(item);
    return rc;
}

/* Resolve a name test to a static tag atom, or TH_TAG_UNKNOWN for a name no known
   HTML tag carries (non-ASCII, mixed case, or simply unknown); such a name then
   matches only unknown-atom elements with that exact spelling. */
static uint16_t resolve_tag_atom(const Py_UCS4 *name, Py_ssize_t len) {
    char buf[64];
    if (len >= (Py_ssize_t)sizeof(buf)) { /* a name this long is no known tag; the parser never makes an empty one */
        return TH_TAG_UNKNOWN;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (name[index] >= 0x80) {
            return TH_TAG_UNKNOWN;
        }
        buf[index] = (char)name[index];
    }
    return th_tag_lookup(buf, len);
}

static uint32_t resolve_attr_atom(struct th_tree *tree, const Py_UCS4 *name, Py_ssize_t len) {
    char buf[128];
    if (len >= (Py_ssize_t)sizeof(buf)) { /* no attribute name is this long; the parser never makes an empty one */
        return UINT32_MAX;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (name[index] >= 0x80) {
            return UINT32_MAX;
        }
        buf[index] = (char)name[index];
    }
    return th_attr_lookup(tree, buf, len);
}

int xp_single_attribute_atom(const xp_program *prog, struct th_tree *tree, uint32_t *atom) {
    const xn *path = &prog->nodes[prog->root];
    if (path->kind != XN_PATH || path->absolute || path->second >= 0) {
        return 0;
    }
    const xn *step = &prog->nodes[path->first];
    if (step->axis != AX_ATTRIBUTE || step->test != NT_NAME || step->prefix_len != 0 || step->first >= 0 ||
        step->next >= 0) {
        return 0;
    }
    *atom = resolve_attr_atom(tree, step->str, step->str_len);
    return 1;
}

/* A step's name test resolved once before the axis walk: the local part (the suffix
   after any "prefix:"), its interned atoms, and the namespace constraint a bound prefix
   adds. With no prefix the test matches by local name in any namespace, as before. */
typedef struct {
    uint16_t atom;      /* tag atom of the local name (element axes), else 0 */
    uint32_t attr_atom; /* attr atom of the local name (attribute axis), else UINT32_MAX */
    const Py_UCS4 *local;
    Py_ssize_t local_len;
    int has_ns;         /* the test carried a namespace prefix */
    int ns_unmatchable; /* the bound URI names no namespace an HTML tree's elements carry */
    int xml_ns;         /* the prefix binds the XML namespace (xml:lang, xml:space) */
    int foreign_only;   /* an attribute test that only a foreign element of an HTML tree can satisfy */
    uint8_t want_ns;    /* enum th_ns an element must carry when has_ns and not unmatchable */
    xp_name_test_fn name_test;
    void *name_test_ctx;
    const Py_UCS4 *qualified;
    Py_ssize_t qualified_len;
    int strict_no_ns;
} step_match;

static int ucs4_eq_ascii(const Py_UCS4 *text, Py_ssize_t text_len, const char *ascii, Py_ssize_t ascii_len) {
    if (text_len != ascii_len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < text_len; index++) {
        if (text[index] != (Py_UCS4)(unsigned char)ascii[index]) {
            return 0;
        }
    }
    return 1;
}

static int element_name_matches(struct th_node *node, const step_match *match) {
    if (match->name_test != NULL) {
        return match->name_test(match->name_test_ctx, node, -1, match->qualified, match->qualified_len);
    }
    /* An HTML element with a builtin atom is spelled as that atom, so the atoms decide.
       Every other element compares its spelling: an XML tree interns every element as
       TH_TAG_UNKNOWN, and a foreign element keeps the case the tree builder gave it, so
       the builtin foreignobject atom names an SVG element spelled foreignObject. */
    if (node->atom != TH_TAG_UNKNOWN && node->ns == TH_NS_HTML) {
        if (node->atom != match->atom) {
            return 0;
        }
    } else if (node->text_len != match->local_len ||
               memcmp(node->text, match->local, (size_t)match->local_len * sizeof(Py_UCS4)) != 0) {
        return 0;
    }
    if (match->strict_no_ns && node->ns != TH_NS_HTML) {
        return 0;
    }
    if (!match->has_ns) {
        return 1;
    }
    return !match->ns_unmatchable && node->ns == match->want_ns;
}

/* Whether a node satisfies a step's node test on an element-principal axis. */
static int node_test_matches(struct th_node *node, const xn *step, const step_match *match) {
    switch (step->test) {
    case NT_NAME:
        return node->type == TH_NODE_ELEMENT ? element_name_matches(node, match) : 0;
    case NT_STAR:
        return node->type == TH_NODE_ELEMENT;
    case NT_TEXT:
        return node->type == TH_NODE_TEXT;
    case NT_COMMENT:
        return node->type == TH_NODE_COMMENT;
    case NT_PI:
        return node->type == TH_NODE_PI;
    default: /* NT_NODE */
        return 1;
    }
}

static struct th_node *descendant_next(struct th_node *node, struct th_node *root) {
    if (node->first_child != NULL) {
        return node->first_child;
    }
    while (node != root && node->next_sibling == NULL) {
        node = node->parent;
    }
    return node == root ? NULL : node->next_sibling;
}

/* Pre-order successor over the whole tree, or NULL past the last node. */
struct th_node *document_next(struct th_node *node) {
    if (node->first_child != NULL) {
        return node->first_child;
    }
    while (node->parent != NULL && node->next_sibling == NULL) {
        node = node->parent;
    }
    return node->next_sibling;
}

struct th_node *tree_root(struct th_tree *tree, struct th_node *node) {
    struct th_node *document = th_tree_document(tree);
    while (node != document && node->parent != NULL) {
        node = node->parent;
    }
    return node;
}

static int is_ancestor_of(struct th_node *candidate, struct th_node *node) {
    for (struct th_node *parent = node->parent; parent != NULL; parent = parent->parent) {
        if (parent == candidate) {
            return 1;
        }
    }
    return 0;
}

/* Reverse the items in [from, out->len) in place (turns the document-order run a
   forward walk produced into the reverse-document proximity order). */
static void reverse_items(xp_nodeset *out, Py_ssize_t from) {
    Py_ssize_t high = out->len - 1;
    while (from < high) {
        xp_item swap = out->items[from];
        out->items[from] = out->items[high];
        out->items[high] = swap;
        from++;
        high--;
    }
}

/* Push node when it passes the step's node test. Returns 0, or -1 on allocation
   failure (which cannot be forced from a test). */
static int emit_if_match(xp_nodeset *out, struct th_node *node, const xn *step, const step_match *match) {
    int matched = node_test_matches(node, step, match);
    if (matched <= 0) {
        return matched;
    }
    return ns_append(out, node, -1);
}

static int apply_descendant_step(xp_nodeset *out, struct th_node *root, const xn *step, const step_match *match,
                                 const xp_nodeset *contexts, Py_ssize_t *covered) {
    while (*covered < contexts->len && contexts->items[*covered].node == root) {
        (*covered)++;
    }
    struct th_node *node = step->axis == AX_DESCENDANT_OR_SELF ? root : root->first_child;
    for (; node != NULL; node = descendant_next(node, root)) {
        while (*covered < contexts->len && contexts->items[*covered].node == node) {
            (*covered)++;
        }
        if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                   /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

static int apply_step(xp_nodeset *out, struct th_node *ctx, enum xp_axis axis, const xn *step, const step_match *match,
                      xp_ctx *context) {
    switch (axis) {
    case AX_ATTRIBUTE: {
        /* node() and * both match every attribute; a name test matches by atom; a
           prefixed name test matches only an xml:-prefixed attribute in the XML
           namespace, which an XML tree or a foreign element carries (the HTML parser
           leaves xml:lang on an HTML element in no namespace); text()/comment()/
           processing-instruction() match no attribute. A non-element context node
           carries no attribute storage, so the axis yields nothing. */
        th_node_attr *attrs;
        Py_ssize_t attr_count = th_node_attributes(ctx, &attrs);
        int eligible = !match->foreign_only || ctx->ns != TH_NS_HTML;
        for (Py_ssize_t index = 0; index < attr_count; index++) {
            int hit = step->test == NT_STAR || step->test == NT_NODE ||
                      (step->test == NT_NAME && eligible && attrs[index].name_atom == match->attr_atom);
            if (step->test == NT_NAME && match->name_test != NULL) {
                hit = match->name_test(match->name_test_ctx, ctx, index, match->qualified, match->qualified_len);
                if (hit < 0) { /* GCOVR_EXCL_BR_LINE: prefix resolution ran before the walk; only OOM remains */
                    return -1; /* GCOVR_EXCL_LINE */
                }
            }
            if (hit) {
                if (push_attribute(out, context, ctx, index) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    return -1;                                      /* GCOVR_EXCL_LINE */
                }
            }
        }
        return 0;
    }
    case AX_SELF:
        return emit_if_match(out, ctx, step, match);
    case AX_CHILD:
        for (struct th_node *child = ctx->first_child; child != NULL; child = child->next_sibling) {
            if (emit_if_match(out, child, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                    /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    case AX_DESCENDANT_OR_SELF:
        if (emit_if_match(out, ctx, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                  /* GCOVR_EXCL_LINE */
        }
        TH_FALLTHROUGH; /* descendant-or-self is self plus the descendant walk */
    case AX_DESCENDANT:
        for (struct th_node *node = ctx->first_child; node != NULL; node = descendant_next(node, ctx)) {
            if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                   /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    case AX_PARENT:
        return ctx->parent != NULL ? emit_if_match(out, ctx->parent, step, match) : 0;
    case AX_ANCESTOR_OR_SELF:
        if (emit_if_match(out, ctx, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                  /* GCOVR_EXCL_LINE */
        }
        TH_FALLTHROUGH; /* ancestor-or-self is self plus the ancestor walk */
    case AX_ANCESTOR:
        for (struct th_node *node = ctx->parent; node != NULL; node = node->parent) {
            if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                   /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    case AX_FOLLOWING_SIBLING:
        for (struct th_node *node = ctx->next_sibling; node != NULL; node = node->next_sibling) {
            if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                   /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    case AX_FOLLOWING: {
        /* everything after ctx's whole subtree, in document order */
        struct th_node *start = NULL;
        for (struct th_node *up = ctx; up != NULL && start == NULL; up = up->parent) {
            start = up->next_sibling;
        }
        for (struct th_node *node = start; node != NULL; node = document_next(node)) {
            if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                   /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    }
    case AX_PRECEDING: {
        /* everything before ctx in document order except its ancestors; collected
           forward then reversed into the axis's reverse-document proximity order */
        struct th_node *root = ctx;
        while (root->parent != NULL) {
            root = root->parent;
        }
        Py_ssize_t from = out->len;
        for (struct th_node *node = root; node != ctx; node = document_next(node)) {
            if (is_ancestor_of(node, ctx)) {
                continue;
            }
            if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                   /* GCOVR_EXCL_LINE */
            }
        }
        reverse_items(out, from);
        return 0;
    }
    case AX_NAMESPACE: {
        /* Every element carries the implicit `xml` namespace node, the only namespace
           node an HTML tree exposes; it is the item with attr == -2. The node test
           matches it for *, node(), and the xml prefix, but not text()/comment()/pi(). */
        int matches = step->test == NT_STAR || step->test == NT_NODE ||
                      (step->test == NT_NAME && step->str_len == 3 && step->str[0] == 'x' && step->str[1] == 'm' &&
                       step->str[2] == 'l');
        if (ctx->type == TH_NODE_ELEMENT && matches) {
            return ns_append(out, ctx, -2);
        }
        return 0;
    }
    default: /* AX_PRECEDING_SIBLING */
        for (struct th_node *node = ctx->prev_sibling; node != NULL; node = node->prev_sibling) {
            if (emit_if_match(out, node, step, match) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                   /* GCOVR_EXCL_LINE */
            }
        }
        return 0;
    }
}

/* An attribute or namespace node has no children and no siblings; its parent is the
   owner element, and in document order it follows the owner and precedes the owner's
   children (XPath 1.0 §5). Only node() matches it on the self axis, whose principal
   node type is element. */
static int apply_owned_step(xp_nodeset *out, xp_item item, const xn *step, const step_match *match) {
    switch (step->axis) {
    case AX_SELF:
    case AX_DESCENDANT_OR_SELF:
        return step->test == NT_NODE ? ns_push(out, item.node, item.attr) : 0;
    case AX_ANCESTOR_OR_SELF:
        if (step->test == NT_NODE && ns_push(out, item.node, item.attr) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                                         /* GCOVR_EXCL_LINE */
        }
        TH_FALLTHROUGH; /* the owner element is the nearest ancestor */
    case AX_ANCESTOR:
        return apply_step(out, item.node, AX_ANCESTOR_OR_SELF, step, match, NULL);
    case AX_PARENT:
        return apply_step(out, item.node, AX_SELF, step, match, NULL);
    case AX_FOLLOWING:
        if (apply_step(out, item.node, AX_DESCENDANT, step, match, NULL) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                                          /* GCOVR_EXCL_LINE */
        }
        return apply_step(out, item.node, AX_FOLLOWING, step, match, NULL);
    case AX_PRECEDING:
        return apply_step(out, item.node, AX_PRECEDING, step, match, NULL);
    default: /* the child, descendant, sibling, attribute, and namespace axes are empty */
        return 0;
    }
}

static Py_ssize_t node_depth(struct th_node *node) {
    Py_ssize_t depth = 0;
    while (node->parent != NULL) {
        node = node->parent;
        depth++;
    }
    return depth;
}

/* Negative when x precedes y in document (pre-order); positive otherwise. Never
   called with x == y. */
static int node_before(struct th_node *left, struct th_node *right) {
    if (left->next_sibling == right || left->first_child == right) {
        return -1;
    }
    if (right->next_sibling == left || right->first_child == left) {
        return 1;
    }
    Py_ssize_t dx = node_depth(left);
    Py_ssize_t dy = node_depth(right);
    struct th_node *walk_left = left;
    struct th_node *walk_right = right;
    for (Py_ssize_t index = dx; index > dy; index--) {
        walk_left = walk_left->parent;
    }
    for (Py_ssize_t index = dy; index > dx; index--) {
        walk_right = walk_right->parent;
    }
    if (walk_left == walk_right) {
        return dx < dy ? -1 : 1; /* the shallower original node is an ancestor of the other */
    }
    while (walk_left->parent != walk_right->parent) {
        walk_left = walk_left->parent;
        walk_right = walk_right->parent;
    }
    for (struct th_node *sibling = walk_left->next_sibling; sibling != NULL; sibling = sibling->next_sibling) {
        if (sibling == walk_right) {
            return -1;
        }
    }
    return 1;
}

/* Order items sharing a node: the node itself first, then its attributes, then its
   namespace node, mirroring document order within an element. */
static Py_ssize_t item_rank(Py_ssize_t attr) {
    if (attr == -1) {
        return 0; /* the node */
    }
    if (attr == -2) {
        return PY_SSIZE_T_MAX; /* the namespace node, after the attributes */
    }
    xp_attribute *snapshot = item_snapshot((xp_item){NULL, attr});
    return (snapshot == NULL ? attr >> 1 : snapshot->ordinal) + 1;
}

static int item_cmp(const void *pa, const void *pb) {
    const xp_item *left = pa;
    const xp_item *right = pb;
    if (left->node == right->node) {
        if (left->attr == right->attr) {
            return 0;
        }
        Py_ssize_t left_rank = item_rank(left->attr);
        Py_ssize_t right_rank = item_rank(right->attr);
        if (left_rank != right_rank) {
            return left_rank < right_rank ? -1 : 1;
        }
        uint32_t left_name = xp_item_attribute(NULL, *left)->name_atom;
        uint32_t right_name = xp_item_attribute(NULL, *right)->name_atom;
        return left_name < right_name ? -1 : 1;
    }
    return node_before(left->node, right->node);
}

static int sort_unique(xp_nodeset *ns) {
    if (ns->len < 2) {
        return 0;
    }
    if (ns->snapshots) {
        size_t capacity = 8;
        while (capacity / 2 < (size_t)ns->len) {
            capacity *= 2;
        }
        xp_item *entries = PyMem_Calloc(capacity, sizeof(*entries));
        if (entries == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;         /* GCOVR_EXCL_LINE */
        }
        Py_ssize_t write = 0;
        for (Py_ssize_t index = 0; index < ns->len; index++) {
            xp_item item = ns->items[index];
            size_t slot = xp_item_hash(item) & (capacity - 1);
            while (entries[slot].node != NULL && !xp_item_equal(entries[slot], item)) {
                slot = (slot + 1) & (capacity - 1);
            }
            if (entries[slot].node == NULL) {
                entries[slot] = item;
                ns->items[write++] = item;
            } else {
                item_release(item);
            }
        }
        PyMem_Free(entries);
        ns->len = write;
    }
    /* Forward axes already produce tree order; preserve it without sorting again. */
    for (Py_ssize_t index = 1; index < ns->len; index++) {
        if (item_cmp(&ns->items[index - 1], &ns->items[index]) > 0) {
            qsort(ns->items, (size_t)ns->len, sizeof(xp_item), item_cmp);
            break;
        }
    }
    if (ns->snapshots) {
        return 0;
    }
    Py_ssize_t write_pos = 1;
    for (Py_ssize_t read_pos = 1; read_pos < ns->len; read_pos++) {
        if (ns->items[read_pos].node != ns->items[write_pos - 1].node ||
            ns->items[read_pos].attr != ns->items[write_pos - 1].attr) {
            ns->items[write_pos++] = ns->items[read_pos];
        }
    }
    ns->len = write_pos;
    return 0;
}

void xp_result_free(xp_result *result) {
    if (result->kind == XP_STRING) {
        PyMem_Free(result->string);
        result->string = NULL;
    } else if (result->kind == XP_NODESET) {
        xp_nodeset_free(&result->nodes);
    }
}

void result_bool(xp_result *result, int value) {
    memset(result, 0, sizeof(*result));
    result->kind = XP_BOOLEAN;
    result->boolean = value != 0;
}

void result_number(xp_result *result, double value) {
    memset(result, 0, sizeof(*result));
    result->kind = XP_NUMBER;
    result->number = value;
}

void result_string(xp_result *result, Py_UCS4 *owned, Py_ssize_t len) {
    memset(result, 0, sizeof(*result));
    result->kind = XP_STRING;
    result->string = owned;
    result->string_len = len;
}

Py_UCS4 *ucs4_dup(const Py_UCS4 *src, Py_ssize_t len) {
    Py_UCS4 *buf = PyMem_Malloc((size_t)len * sizeof(Py_UCS4));
    if (buf == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return NULL;   /* GCOVR_EXCL_LINE */
    }
    if (len) {
        memcpy(buf, src, (size_t)len * sizeof(Py_UCS4));
    }
    return buf;
}

Py_UCS4 *ucs4_from_ascii(const char *src, Py_ssize_t length, Py_ssize_t *len) {
    *len = length;
    Py_UCS4 *buffer = PyMem_Malloc((size_t)length * sizeof(Py_UCS4));
    if (buffer == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < length; index++) {
        buffer[index] = (Py_UCS4)(unsigned char)src[index];
    }
    return buffer;
}

Py_UCS4 *item_string(struct th_tree *tree, xp_item item, Py_ssize_t *len) {
    if (item.attr == -2) {
        return ucs4_from_ascii(XP_XML_NS_URI, sizeof(XP_XML_NS_URI) - 1, len);
    }
    if (xp_is_attribute(item.attr)) {
        const th_node_attr *attr_record = xp_item_attribute(tree, item);
        *len = attr_record->value == NULL ? 0 : attr_record->value_len;
        return ucs4_dup(attr_record->value, *len);
    }
    if (item.node->type == TH_NODE_TEXT || item.node->type == TH_NODE_COMMENT) {
        return th_node_data(tree, item.node, len);
    }
    return th_node_text(tree, item.node, len);
}

/* Clinger's fast path: a digit run whose mantissa fits 2^53 with at most 22 digits after the point is
   mantissa / 10^fraction_digits, two exact doubles divided with a single rounding. */
#define XP_EXACT_MANTISSA (UINT64_C(1) << 53)
#define XP_EXACT_FRACTION_DIGITS 22
static const double XP_POWERS_OF_TEN[] = {1e0,  1e1,  1e2,  1e3,  1e4,  1e5,  1e6,  1e7,  1e8,  1e9,  1e10, 1e11,
                                          1e12, 1e13, 1e14, 1e15, 1e16, 1e17, 1e18, 1e19, 1e20, 1e21, 1e22};

/* A decimal run too long for the exact fast path, converted by CPython's correctly
   rounded strtod. The run is ASCII digits and at most one '.', which Python's float
   grammar accepts, so only an allocation failure can make the conversion fail. */
static double decimal_value_slow(const Py_UCS4 *digits, Py_ssize_t len) {
    char *ascii = PyMem_Malloc((size_t)len + 1);
    if (ascii == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return (double)NAN; /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        ascii[index] = (char)digits[index];
    }
    ascii[len] = '\0';
    double value = PyOS_string_to_double(ascii, NULL, NULL);
    PyMem_Free(ascii);
    if (value == -1.0 && PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: only an allocation failure */
        PyErr_Clear();                       /* GCOVR_EXCL_LINE */
        return (double)NAN;                  /* GCOVR_EXCL_LINE */
    }
    return value;
}

double xp_decimal_value(const Py_UCS4 *digits, Py_ssize_t len) {
    uint64_t mantissa = 0;
    Py_ssize_t fraction_digits = 0;
    int after_dot = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (digits[index] == '.') {
            after_dot = 1;
            continue;
        }
        mantissa = mantissa * 10 + (digits[index] - '0');
        fraction_digits += after_dot;
        if (mantissa > XP_EXACT_MANTISSA || fraction_digits > XP_EXACT_FRACTION_DIGITS) {
            return decimal_value_slow(digits, len);
        }
    }
    return (double)mantissa / XP_POWERS_OF_TEN[fraction_digits];
}

/* XPath number parse: optional leading/trailing whitespace around an optional sign,
   digits, and attr_record fractional part; anything else is NaN. */
double parse_number(const Py_UCS4 *text, Py_ssize_t len) {
    Py_ssize_t index = 0;
    while (index < len && xp_is_space(text[index])) {
        index++;
    }
    Py_ssize_t end = len;
    while (end > index && xp_is_space(text[end - 1])) {
        end--;
    }
    if (index == end) {
        return (double)NAN;
    }
    int sign = 1;
    if (text[index] == '-') {
        sign = -1;
        index++;
    }
    uint64_t mantissa = 0;
    Py_ssize_t fraction_digits = 0;
    int exact = 1;
    int seen_digit = 0;
    int after_dot = 0;
    for (Py_ssize_t cursor = index; cursor < end; cursor++) {
        Py_UCS4 ch = text[cursor];
        if (ch == '.' && !after_dot) {
            after_dot = 1;
        } else if (ch >= '0' && ch <= '9') {
            seen_digit = 1;
            if (exact) {
                mantissa = mantissa * 10 + (ch - '0');
                fraction_digits += after_dot;
                exact = mantissa <= XP_EXACT_MANTISSA && fraction_digits <= XP_EXACT_FRACTION_DIGITS;
            }
        } else {
            return (double)NAN;
        }
    }
    if (!seen_digit) {
        return (double)NAN;
    }
    if (!exact) {
        return sign * decimal_value_slow(text + index, end - index);
    }
    return sign * (fraction_digits == 0 ? (double)mantissa : (double)mantissa / XP_POWERS_OF_TEN[fraction_digits]);
}

/* Render a finite non-integer (or a large integer past the %lld fast path) as the plain
   decimal XPath 1.0 §4.2 requires: the fewest significant digits that round-trip the
   double, never an exponent. PyOS_double_to_string with the 'r' code gives the shortest
   round-tripping digits (the same repr Python's float uses); this expands its mantissa
   and decimal exponent into positional notation. Returns the written length, or -1 with
   a Python exception set on allocation failure. */
static Py_ssize_t decimal_expand(double value, char *out) {
    char *repr = PyOS_double_to_string(value, 'r', 0, 0, NULL);
    if (repr == NULL) { /* GCOVR_EXCL_BR_LINE: the repr allocation cannot be forced to fail */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    char digits[32];
    Py_ssize_t written = 0;
    const char *cursor = repr;
    if (*cursor == '-') {
        out[written++] = '-';
        cursor++;
    }
    Py_ssize_t digit_count = 0;
    Py_ssize_t point = -1; /* index in digits of the decimal point, or the end when absent */
    for (; *cursor != '\0' && *cursor != 'e'; cursor++) { /* the 'r' code always spells the exponent lowercase */
        if (*cursor == '.') {
            point = digit_count;
        } else {
            digits[digit_count++] = *cursor;
        }
    }
    if (point < 0) {
        point = digit_count;
    }
    Py_ssize_t exponent = *cursor == '\0' ? 0 : (Py_ssize_t)strtol(cursor + 1, NULL, 10);
    PyMem_Free(repr);
    Py_ssize_t split = point + exponent; /* digits left of the point after applying the exponent */
    if (split <= 0) {
        out[written++] = '0';
        out[written++] = '.';
        for (Py_ssize_t index = 0; index < -split; index++) {
            out[written++] = '0';
        }
        for (Py_ssize_t index = 0; index < digit_count; index++) {
            out[written++] = digits[index];
        }
    } else if (split >= digit_count) {
        for (Py_ssize_t index = 0; index < digit_count; index++) {
            out[written++] = digits[index];
        }
        for (Py_ssize_t index = 0; index < split - digit_count; index++) {
            out[written++] = '0';
        }
    } else {
        for (Py_ssize_t index = 0; index < split; index++) {
            out[written++] = digits[index];
        }
        out[written++] = '.';
        for (Py_ssize_t index = split; index < digit_count; index++) {
            out[written++] = digits[index];
        }
    }
    return written;
}

static int format_number(double value, Py_UCS4 **out, Py_ssize_t *out_len) {
    char buf[512]; /* the widest finite double expands to ~325 decimal digits */
    Py_ssize_t len;
    if (isnan(value)) { /* GCOVR_EXCL_BR_LINE: dead type-dispatch arm of the isnan macro */
        len = snprintf(buf, sizeof(buf), "NaN");
    } else if (isinf(value)) { /* GCOVR_EXCL_BR_LINE: dead type-dispatch arm of the isinf macro */
        len = snprintf(buf, sizeof(buf), "%s", value < 0 ? "-Infinity" : "Infinity");
    } else if (fabs(value) < 1e15 && value == (double)(long long)value) {
        len = snprintf(buf, sizeof(buf), "%lld", (long long)value);
    } else {
        len = decimal_expand(value, buf);
        if (len < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1; /* GCOVR_EXCL_LINE */
        }
    }
    Py_UCS4 *buffer = PyMem_Malloc((size_t)len * sizeof(Py_UCS4));
    if (buffer == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        buffer[index] = (Py_UCS4)(unsigned char)buf[index];
    }
    *out = buffer;
    *out_len = len;
    return 0;
}

int to_boolean(struct th_tree *tree, const xp_result *value) {
    switch (value->kind) {
    case XP_BOOLEAN:
        return value->boolean;
    case XP_NUMBER:
        return value->number != 0 && !isnan(value->number);
    case XP_STRING:
        return value->string_len > 0;
    default: /* XP_NODESET */
        (void)tree;
        return value->nodes.len > 0;
    }
}

/* The string-value of value, freshly allocated; *len receives the length. */
Py_UCS4 *to_string(struct th_tree *tree, const xp_result *value, Py_ssize_t *len) {
    switch (value->kind) {
    case XP_STRING:
        *len = value->string_len;
        return ucs4_dup(value->string, value->string_len);
    case XP_BOOLEAN: {
        const char *literal = value->boolean ? "true" : "false";
        *len = (Py_ssize_t)strlen(literal);
        Py_UCS4 *buffer = PyMem_Malloc((size_t)*len * sizeof(Py_UCS4));
        if (buffer == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
            return NULL;      /* GCOVR_EXCL_LINE */
        }
        for (Py_ssize_t index = 0; index < *len; index++) {
            buffer[index] = (Py_UCS4)(unsigned char)literal[index];
        }
        return buffer;
    }
    case XP_NUMBER: {
        Py_UCS4 *buffer = NULL;
        if (format_number(value->number, &buffer, len) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return NULL;                                      /* GCOVR_EXCL_LINE */
        }
        return buffer;
    }
    default: /* XP_NODESET: string-value of the first node in document order */
        if (value->nodes.len == 0) {
            *len = 0;
            return ucs4_dup(NULL, 0);
        }
        return item_string(tree, value->nodes.items[0], len);
    }
}

double to_number(struct th_tree *tree, const xp_result *value) {
    if (value->kind == XP_NUMBER) {
        return value->number;
    }
    if (value->kind == XP_BOOLEAN) {
        return value->boolean ? 1.0 : 0.0;
    }
    Py_ssize_t len;
    Py_UCS4 *text = to_string(tree, value, &len);
    if (text == NULL) {     /* GCOVR_EXCL_BR_LINE: alloc */
        return (double)NAN; /* GCOVR_EXCL_LINE */
    }
    double number = parse_number(text, len);
    PyMem_Free(text);
    return number;
}

/* Apply each predicate in the XN_PRED chain to set, in place, in proximity order. */
static int apply_predicates(const xp_program *prog, int32_t pred_head, xp_ctx *ctx, xp_nodeset *set) {
    for (int32_t pr = pred_head; pr >= 0; pr = prog->nodes[pr].next) {
        int32_t expr = prog->nodes[pr].first;
        const xn *predicate = &prog->nodes[expr];
        static const Py_UCS4 last[] = {'l', 'a', 's', 't'};
        if (predicate->kind == XN_NUM || (predicate->kind == XN_FUNC && predicate->first < 0 &&
                                          predicate->str_len == 4 && memcmp(predicate->str, last, sizeof(last)) == 0)) {
            double position = predicate->kind == XN_NUM ? predicate->num : (double)set->len;
            Py_ssize_t selected = position >= 1 && position <= (double)set->len && floor(position) == position
                                      ? (Py_ssize_t)position - 1
                                      : -1;
            xp_item item = selected >= 0 ? set->items[selected] : (xp_item){NULL, -1};
            if (set->snapshots) {
                for (Py_ssize_t index = 0; index < set->len; index++) {
                    if (index != selected) {
                        item_release(set->items[index]);
                    }
                    set->items[index] = (xp_item){NULL, -1};
                }
            }
            set->len = selected >= 0;
            if (set->len) {
                set->items[0] = item;
            }
            continue;
        }
        Py_ssize_t size = set->len;
        Py_ssize_t write_pos = 0;
        for (Py_ssize_t index = 0; index < set->len; index++) {
            xp_ctx pctx = {ctx->tree,
                           set->items[index].node,
                           set->items[index].attr,
                           index + 1,
                           size,
                           ctx->feature,
                           ctx->vars,
                           ctx->namespaces,
                           ctx->extension,
                           ctx->extension_ctx,
                           ctx->depth,
                           ctx->regex_cache,
                           ctx->live,
                           ctx->before_python,
                           ctx->name_test,
                           ctx->name_test_ctx,
                           ctx->strict_no_ns};
            xp_result value;
            int rc = eval_expr(prog, expr, &pctx, &value);
            if (rc < 0) {
                return rc;
            }
            int keep = value.kind == XP_NUMBER ? (double)(index + 1) == value.number : to_boolean(ctx->tree, &value);
            xp_result_free(&value);
            if (keep) {
                set->items[write_pos] = set->items[index];
                if (set->snapshots && write_pos != index) {
                    set->items[index] = (xp_item){NULL, -1};
                }
                write_pos++;
            } else if (set->snapshots) {
                item_release(set->items[index]);
                set->items[index] = (xp_item){NULL, -1};
            }
        }
        set->len = write_pos;
    }
    return 0;
}

/* Resolve a name-test prefix to the element namespace it constrains. On success fills
   match->want_ns (the enum th_ns an element must carry) and match->ns_unmatchable (set
   when the bound URI names no namespace an HTML tree's elements live in, e.g. a custom
   or the XML URI). Returns 0, or -1 when the prefix has no binding. The XML prefix is
   implicitly bound even without a mapping, matching lxml. */
static int resolve_step_ns(xp_ctx *ctx, const Py_UCS4 *prefix, Py_ssize_t prefix_len, step_match *match) {
    const Py_UCS4 *uri = NULL;
    Py_ssize_t uri_len = 0;
    int found = 0;
    if (ctx->namespaces != NULL) {
        for (Py_ssize_t index = 0; index < ctx->namespaces->len; index++) {
            const xp_namespace *binding = &ctx->namespaces->items[index];
            if (binding->prefix_len == prefix_len &&
                memcmp(binding->prefix, prefix, (size_t)prefix_len * sizeof(Py_UCS4)) == 0) {
                uri = binding->uri;
                uri_len = binding->uri_len;
                found = 1;
                break;
            }
        }
    }
    if (!found) {
        if (ucs4_eq_ascii(prefix, prefix_len, XP_XML_NS_PREFIX, sizeof(XP_XML_NS_PREFIX) - 1)) {
            match->ns_unmatchable = 1; /* no element in an HTML tree is in the xml namespace */
            match->xml_ns = 1;
            return 0;
        }
        return -1;
    }
    if (ucs4_eq_ascii(uri, uri_len, XP_XML_NS_URI, sizeof(XP_XML_NS_URI) - 1)) {
        match->ns_unmatchable = 1;
        match->xml_ns = 1;
    } else if (ucs4_eq_ascii(uri, uri_len, XP_SVG_NS_URI, sizeof(XP_SVG_NS_URI) - 1)) {
        match->want_ns = TH_NS_SVG;
    } else if (ucs4_eq_ascii(uri, uri_len, XP_MATHML_NS_URI, sizeof(XP_MATHML_NS_URI) - 1)) {
        match->want_ns = TH_NS_MATHML;
    } else if (uri_len == 0) {
        match->want_ns = TH_NS_HTML;
    } else {
        match->ns_unmatchable = 1;
    }
    return 0;
}

/* Resolve a step's name test once: the local part after any prefix, its interned atoms,
   and the namespace the prefix binds. Returns 0, or -3 (with *feature set) for an
   unbound prefix. */
static int build_step_match(xp_ctx *ctx, const xn *step, step_match *match) {
    memset(match, 0, sizeof(*match));
    match->attr_atom = UINT32_MAX;
    match->local = step->str;
    match->local_len = step->str_len;
    match->qualified = step->str;
    match->qualified_len = step->str_len;
    match->name_test = ctx->name_test;
    match->name_test_ctx = ctx->name_test_ctx;
    match->strict_no_ns = ctx->strict_no_ns && step->prefix_len == 0; /* GCOVR_EXCL_BR_LINE: prefixes use callback */
    if (step->test != NT_NAME) {
        return 0;
    }
    if (step->prefix_len > 0) {
        match->has_ns = 1;
        match->local = step->str + step->prefix_len + 1;
        match->local_len = step->str_len - step->prefix_len - 1;
        if (ctx->name_test == NULL && resolve_step_ns(ctx, step->str, step->prefix_len, match) < 0) {
            *ctx->feature = "undefined namespace prefix";
            return -3;
        }
        if (ctx->name_test != NULL && ctx->name_test(ctx->name_test_ctx, NULL, -1, step->str, step->str_len) < 0) {
            return -1;
        }
    }
    if (step->axis == AX_ATTRIBUTE && match->xml_ns) {
        /* both trees store an XML-namespace attribute under its xml:-prefixed name */
        Py_UCS4 qualified[128] = {'x', 'm', 'l', ':'};
        if (match->local_len < (Py_ssize_t)(sizeof(qualified) / sizeof(qualified[0])) - 4) {
            memcpy(qualified + 4, match->local, (size_t)match->local_len * sizeof(Py_UCS4));
            match->attr_atom = resolve_attr_atom(ctx->tree, qualified, match->local_len + 4);
        }
        match->foreign_only = !th_tree_is_xml(ctx->tree);
    } else if (step->axis == AX_ATTRIBUTE) {
        /* a prefix that binds any other namespace names no attribute a tree carries */
        if (!match->has_ns) {
            match->attr_atom = resolve_attr_atom(ctx->tree, match->local, match->local_len);
        }
    } else {
        match->atom = resolve_tag_atom(match->local, match->local_len);
    }
    return 0;
}

static int eval_path_step(const xp_program *prog, const xn *step, const step_match *match, xp_ctx *ctx,
                          xp_nodeset *current, xp_nodeset *following);
static int eval_path_step_inner(const xp_program *prog, const xn *step, const step_match *match, xp_ctx *ctx,
                                xp_nodeset *current, xp_nodeset *following, xp_nodeset *accepted);

static int eval_path_inner(const xp_program *prog, int32_t path_idx, xp_ctx *ctx, xp_nodeset *out, xp_nodeset *current,
                           xp_nodeset *following) {
    const xn *root = &prog->nodes[path_idx];
    xp_item start;
    xp_nodeset initial;
    xp_nodeset *contexts = current;
    if (root->second >= 0) {
        xp_result base;
        int rc = eval_expr(prog, root->second, ctx, &base);
        if (rc < 0) {
            return rc;
        }
        if (base.kind != XP_NODESET) {
            xp_result_free(&base);
            *ctx->feature = "a path step on a non-node-set";
            return -4;
        }
        *current = base.nodes; /* take ownership */
    } else {
        start =
            (xp_item){root->absolute ? tree_root(ctx->tree, ctx->node) : ctx->node, root->absolute ? -1 : ctx->attr};
        if (root->first < 0) {
            if (ns_push(current, start.node, start.attr) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                      /* GCOVR_EXCL_LINE */
            }
        } else {
            initial = (xp_nodeset){.items = &start, .len = 1};
            contexts = &initial;
        }
    }
    for (int32_t si = root->first; si >= 0; si = prog->nodes[si].next) {
        const xn *step = &prog->nodes[si];
        step_match match;
        int resolved = build_step_match(ctx, step, &match);
        if (resolved < 0) {
            xp_nodeset_free(current);
            xp_nodeset_free(following);
            return resolved;
        }
        if (following->snapshots) {
            for (Py_ssize_t index = 0; index < following->len; index++) {
                item_release(following->items[index]);
            }
            following->snapshots = 0;
        }
        following->len = 0;
        /* One descendant walk cannot reorder or repeat a node. */
        int ordered_unique =
            contexts->len == 1 && contexts->items[0].attr == -1 && step->axis == AX_DESCENDANT && step->first < 0;
        int stepped = eval_path_step(prog, step, &match, ctx, contexts, following);
        if (stepped < 0) {
            xp_nodeset_free(current);
            xp_nodeset_free(following);
            return stepped;
        }
        xp_nodeset swap = *current;
        *current = *following;
        *following = swap;
        contexts = current;
        if (!ordered_unique && sort_unique(current) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            xp_nodeset_free(current);                      /* GCOVR_EXCL_LINE */
            xp_nodeset_free(following);                    /* GCOVR_EXCL_LINE */
            return -1;                                     /* GCOVR_EXCL_LINE */
        }
    }
    xp_nodeset_free(following);
    *out = *current;
    return 0;
}

static int eval_path_step(const xp_program *prog, const xn *step, const step_match *match, xp_ctx *ctx,
                          xp_nodeset *current, xp_nodeset *following) {
    if (ctx->live == NULL) {
        return eval_path_step_inner(prog, step, match, ctx, current, following, NULL);
    }
    xp_nodeset accepted = {0};
    xp_live_frame frame = {.sets = {current}, .append = &accepted};
    xp_live_enter(ctx, &frame);
    int rc = eval_path_step_inner(prog, step, match, ctx, current, following, &accepted);
    xp_live_leave(ctx, &frame);
    return rc;
}

static int eval_path_step_inner(const xp_program *prog, const xn *step, const step_match *match, xp_ctx *ctx,
                                xp_nodeset *current, xp_nodeset *following, xp_nodeset *accepted) {
    /* Mark covered contexts during the subtree walk: checking ancestry per context is quadratic on deep trees. */
    int skip_nested =
        current->len > 1 && step->first < 0 && (step->axis == AX_DESCENDANT || step->axis == AX_DESCENDANT_OR_SELF);
    Py_ssize_t covered = 0;
    for (Py_ssize_t index = 0; index < current->len; index++) {
        Py_ssize_t before = following->len;
        xp_item item = current->items[index];
        int stepped;
        if (skip_nested && item.attr == -1) {
            if (index < covered) {
                continue;
            }
            covered = index + 1;
            stepped = apply_descendant_step(following, item.node, step, match, current, &covered);
        } else {
            stepped = item.attr == -1 ? apply_step(following, item.node, step->axis, step, match, ctx)
                                      : apply_owned_step(following, item, step, match);
        }
        if (stepped < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;     /* GCOVR_EXCL_LINE */
        }
        if (step->first >= 0) {
            xp_nodeset slice = {following->items + before, following->len - before, 0, following->snapshots};
            int rc;
            if (accepted != NULL) {
                *accepted = *following;
                accepted->len = before;
                xp_live_frame frame = {.sets = {&slice}};
                xp_live_enter(ctx, &frame);
                rc = apply_predicates(prog, step->first, ctx, &slice);
                xp_live_leave(ctx, &frame);
            } else {
                rc = apply_predicates(prog, step->first, ctx, &slice);
            }
            if (rc < 0) {
                return rc;
            }
            following->len = before + slice.len;
        }
    }
    return 0;
}

static int eval_path(const xp_program *prog, int32_t path_idx, xp_ctx *ctx, xp_nodeset *out) {
    xp_nodeset current = {0};
    xp_nodeset following = {0};
    return eval_path_inner(prog, path_idx, ctx, out, &current, &following);
}

/* Existential comparison of two scalar values (neither a node-set). */
static int cmp_scalar(struct th_tree *tree, int op, const xp_result *left, const xp_result *right) {
    if (op == XN_EQ || op == XN_NE) {
        int eq;
        if (left->kind == XP_BOOLEAN || right->kind == XP_BOOLEAN) {
            eq = to_boolean(tree, left) == to_boolean(tree, right);
        } else if (left->kind == XP_NUMBER || right->kind == XP_NUMBER) {
            eq = to_number(tree, left) == to_number(tree, right);
        } else {
            eq = left->string_len == right->string_len &&
                 (left->string_len == 0 ||
                  memcmp(left->string, right->string, (size_t)left->string_len * sizeof(Py_UCS4)) == 0);
        }
        return op == XN_EQ ? eq : !eq;
    }
    double left_num = to_number(tree, left);
    double right_num = to_number(tree, right);
    switch (op) {
    case XN_LT:
        return left_num < right_num;
    case XN_LE:
        return left_num <= right_num;
    case XN_GT:
        return left_num > right_num;
    default: /* XN_GE */
        return left_num >= right_num;
    }
}

/* A node-set member wrapped as left standalone string value. */
static int item_as_string(struct th_tree *tree, xp_item item, xp_result *out) {
    Py_ssize_t len;
    Py_UCS4 *text = item_string(tree, item, &len);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    result_string(out, text, len);
    return 0;
}

static int compare_equal_sets(struct th_tree *tree, const xp_nodeset *left, const xp_nodeset *right, int *result);
static int compare_unequal_sets(struct th_tree *tree, const xp_nodeset *left, const xp_nodeset *right, int *result);
static int compare_ordered_sets(struct th_tree *tree, int op, const xp_nodeset *left, const xp_nodeset *right,
                                int *result);

static int compare(struct th_tree *tree, int op, xp_result *first, xp_result *second, int *result) {
    int a_ns = first->kind == XP_NODESET;
    int b_ns = second->kind == XP_NODESET;
    if (!a_ns && !b_ns) {
        *result = cmp_scalar(tree, op, first, second);
        return 0;
    }
    /* first node-set compared with first boolean uses the node-set's own boolean value */
    if ((a_ns && second->kind == XP_BOOLEAN) || (b_ns && first->kind == XP_BOOLEAN)) {
        *result = cmp_scalar(tree, op, first, second);
        return 0;
    }
    xp_nodeset *left = a_ns ? &first->nodes : NULL;
    xp_nodeset *right = b_ns ? &second->nodes : NULL;
    *result = 0;
    if (a_ns && b_ns) {
        if (op == XN_EQ && left->len >= 16 && right->len >= 16) {
            return compare_equal_sets(tree, left, right, result);
        }
        if (op == XN_NE && left->len > 0 && right->len > 0) {
            return compare_unequal_sets(tree, left, right, result);
        }
        if (op != XN_EQ && op != XN_NE && left->len >= 16 && right->len >= 16) {
            return compare_ordered_sets(tree, op, left, right, result);
        }
        for (Py_ssize_t index = 0; index < left->len && !*result; index++) {
            xp_result si;
            if (item_as_string(tree, left->items[index], &si) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                                           /* GCOVR_EXCL_LINE */
            }
            for (Py_ssize_t inner_index = 0; inner_index < right->len && !*result; inner_index++) {
                xp_result sj;
                if (item_as_string(tree, right->items[inner_index], &sj) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    xp_result_free(&si);                                        /* GCOVR_EXCL_LINE */
                    return -1;                                                  /* GCOVR_EXCL_LINE */
                }
                *result = cmp_scalar(tree, op, &si, &sj);
                xp_result_free(&sj);
            }
            xp_result_free(&si);
        }
        return 0;
    }
    xp_nodeset *ns = a_ns ? left : right;
    xp_result *other = a_ns ? second : first;
    for (Py_ssize_t index = 0; index < ns->len && !*result; index++) {
        xp_result si;
        if (item_as_string(tree, ns->items[index], &si) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;                                         /* GCOVR_EXCL_LINE */
        }
        *result = a_ns ? cmp_scalar(tree, op, &si, other) : cmp_scalar(tree, op, other, &si);
        xp_result_free(&si);
    }
    return 0;
}

static PyObject *comparison_string(struct th_tree *tree, xp_item item);

static int compare_equal_sets(struct th_tree *tree, const xp_nodeset *left, const xp_nodeset *right, int *result) {
    if (left->items[0].node == right->items[0].node && left->items[0].attr == right->items[0].attr) {
        *result = 1;
        return 0;
    }
    PyObject *first = comparison_string(tree, left->items[0]);
    if (first == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;       /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *seen = PySet_New(NULL);
    if (seen == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(first); /* GCOVR_EXCL_LINE: allocation failure */
        return -1;        /* GCOVR_EXCL_LINE: allocation failure */
    }
    int rc = 0;
    for (Py_ssize_t index = 0; index < right->len; index++) {
        PyObject *text = comparison_string(tree, right->items[index]);
        if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            rc = -1;        /* GCOVR_EXCL_LINE: allocation failure */
            break;          /* GCOVR_EXCL_LINE: allocation failure */
        }
        *result = PyObject_RichCompareBool(first, text, Py_EQ);
        if (*result) {
            Py_DECREF(text);
            break;
        }
        rc = PySet_Add(seen, text);
        Py_DECREF(text);
        if (rc < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            break;    /* GCOVR_EXCL_LINE: allocation failure */
        }
    }
    Py_DECREF(first);
    if (rc == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        for (Py_ssize_t index = 1; index < left->len && !*result; index++) {
            PyObject *text = comparison_string(tree, left->items[index]);
            if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                rc = -1;        /* GCOVR_EXCL_LINE: allocation failure */
                break;          /* GCOVR_EXCL_LINE: allocation failure */
            }
            *result = PySet_Contains(seen, text);
            Py_DECREF(text);
        }
    }
    Py_DECREF(seen);
    return rc;
}

static PyObject *comparison_string(struct th_tree *tree, xp_item item) {
    Py_ssize_t len;
    Py_UCS4 *text = item_string(tree, item, &len);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;    /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *value = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, text, len);
    PyMem_Free(text);
    return value;
}

/* Inequality is existential too: it is false only when both nonempty sets contain one shared value. */
static int compare_unequal_sets(struct th_tree *tree, const xp_nodeset *left, const xp_nodeset *right, int *result) {
    xp_result first;
    if (item_as_string(tree, left->items[0], &first) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                          /* GCOVR_EXCL_LINE: allocation failure */
    }
    const xp_nodeset *sets[] = {right, left};
    int rc = 0;
    for (size_t side = 0; side < 2 && !*result; side++) {
        for (Py_ssize_t index = (Py_ssize_t)side; index < sets[side]->len && !*result; index++) {
            xp_result next;
            const int converted = item_as_string(tree, sets[side]->items[index], &next);
            if (converted < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                rc = -1;         /* GCOVR_EXCL_LINE: allocation failure */
                break;           /* GCOVR_EXCL_LINE: allocation failure */
            }
            *result = cmp_scalar(tree, XN_NE, &first, &next);
            xp_result_free(&next);
        }
        if (rc < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            break;    /* GCOVR_EXCL_LINE: allocation failure */
        }
    }
    xp_result_free(&first);
    return rc;
}

static int comparison_number(struct th_tree *tree, xp_item item, double *number);
static int comparison_extreme(struct th_tree *tree, const xp_nodeset *nodes, int minimum, double *value);

static int compare_ordered_sets(struct th_tree *tree, int op, const xp_nodeset *left, const xp_nodeset *right,
                                int *result) {
    xp_result first = {.kind = XP_NUMBER};
    xp_result second = {.kind = XP_NUMBER};
    const int first_rc = comparison_number(tree, left->items[0], &first.number);
    const int second_rc = comparison_number(tree, right->items[0], &second.number);
    if (first_rc < 0 || second_rc < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                       /* GCOVR_EXCL_LINE: allocation failure */
    }
    *result = cmp_scalar(tree, op, &first, &second);
    if (*result) {
        return 0;
    }
    const int less = op == XN_LT || op == XN_LE;
    const int left_rc = comparison_extreme(tree, left, less, &first.number);
    const int right_rc = comparison_extreme(tree, right, !less, &second.number);
    if (left_rc < 0 || right_rc < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                     /* GCOVR_EXCL_LINE: allocation failure */
    }
    *result = cmp_scalar(tree, op, &first, &second);
    return 0;
}

static int comparison_number(struct th_tree *tree, xp_item item, double *number) {
    xp_result text;
    if (item_as_string(tree, item, &text) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                               /* GCOVR_EXCL_LINE: allocation failure */
    }
    *number = to_number(tree, &text);
    xp_result_free(&text);
    return 0;
}

/* A matching pair exists iff the corresponding extrema compare true; NaNs cannot supply a match. */
static int comparison_extreme(struct th_tree *tree, const xp_nodeset *nodes, int minimum, double *value) {
    for (Py_ssize_t index = 1; index < nodes->len; index++) {
        double number;
        if (comparison_number(tree, nodes->items[index], &number) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                                   /* GCOVR_EXCL_LINE: allocation failure */
        }
        const int missing = isnan(*value); /* GCOVR_EXCL_BR_LINE: dead type-dispatch arm of the isnan macro */
        if (missing || (minimum ? number < *value : number > *value)) {
            *value = number;
        }
    }
    return 0;
}

static int nodeset_union(xp_nodeset *target, xp_nodeset *source) {
    /* Extension functions can return unordered nodes and duplicates. */
    if (target->snapshots || source->snapshots) {
        for (Py_ssize_t index = 0; index < source->len; index++) {
            int rc = ns_push(target, source->items[index].node, source->items[index].attr);
            if (rc < 0) {  /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1; /* GCOVR_EXCL_LINE */
            }
        }
        xp_nodeset_free(source);
        return sort_unique(target);
    }
    sort_unique(target);
    sort_unique(source);
    xp_nodeset merged = {0};
    Py_ssize_t left = 0;
    Py_ssize_t right = 0;
    while (left < target->len || right < source->len) {
        const xp_item *item;
        if (left == target->len) {
            item = &source->items[right++];
        } else if (right == source->len) {
            item = &target->items[left++];
        } else {
            const int order = item_cmp(&target->items[left], &source->items[right]);
            item = order <= 0 ? &target->items[left++] : &source->items[right];
            right += order >= 0;
        }
        if (ns_append(&merged, item->node, item->attr) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            xp_nodeset_free(&merged);                         /* GCOVR_EXCL_LINE: allocation failure */
            return -1;                                        /* GCOVR_EXCL_LINE: allocation failure */
        }
    }
    xp_nodeset_free(target);
    xp_nodeset_free(source);
    *target = merged;
    return 0;
}

/* Deep-copy a bound variable's value so the reference owns its result. A node-set
   binding (an Element or an iterable of Elements from the caller) is copied and
   normalized to document order without duplicates, the canonical node-set form the
   rest of the evaluator assumes. */
static int copy_result(const xp_result *src, xp_result *dst) {
    memset(dst, 0, sizeof(*dst));
    dst->kind = src->kind;
    if (src->kind == XP_STRING) {
        dst->string = ucs4_dup(src->string, src->string_len);
        if (dst->string == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;             /* GCOVR_EXCL_LINE */
        }
        dst->string_len = src->string_len;
    } else if (src->kind == XP_NUMBER) {
        dst->number = src->number;
    } else if (src->kind == XP_NODESET) {
        if (src->nodes.len > 0) {
            dst->nodes.items = PyMem_Malloc((size_t)src->nodes.len * sizeof(xp_item));
            if (dst->nodes.items == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;                  /* GCOVR_EXCL_LINE */
            }
            memcpy(dst->nodes.items, src->nodes.items, (size_t)src->nodes.len * sizeof(xp_item));
            dst->nodes.len = src->nodes.len;
            dst->nodes.cap = src->nodes.len;
            sort_unique(&dst->nodes);
        }
    } else {
        dst->boolean = src->boolean;
    }
    return 0;
}

static int eval_expr_inner(const xp_program *prog, int32_t idx, xp_ctx *ctx, xp_result *out) {
    const xn *expr = &prog->nodes[idx];
    switch (expr->kind) {
    case XN_NUM:
        result_number(out, expr->num);
        return 0;
    case XN_LIT: {
        Py_UCS4 *text = ucs4_dup(expr->str, expr->str_len);
        if (text == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;      /* GCOVR_EXCL_LINE */
        }
        result_string(out, text, expr->str_len);
        return 0;
    }
    case XN_VAR: {
        for (Py_ssize_t index = 0; ctx->vars != NULL && index < ctx->vars->len; index++) {
            const xp_binding *binding = &ctx->vars->items[index];
            if (binding->name_len == expr->str_len &&
                memcmp(binding->name, expr->str, (size_t)expr->str_len * sizeof(Py_UCS4)) == 0) {
                return copy_result(&binding->value, out);
            }
        }
        *ctx->feature = "a reference to an unbound variable";
        return -3;
    }
    case XN_PATH: {
        xp_nodeset ns = {0};
        int rc = eval_path(prog, idx, ctx, &ns);
        if (rc < 0) {
            return rc;
        }
        memset(out, 0, sizeof(*out));
        out->kind = XP_NODESET;
        out->nodes = ns;
        return 0;
    }
    case XN_FILTER: {
        xp_result primary;
        int rc = eval_expr(prog, expr->first, ctx, &primary);
        if (rc < 0) {
            return rc;
        }
        if (primary.kind != XP_NODESET) {
            xp_result_free(&primary);
            *ctx->feature = "a predicate on a non-node-set";
            return -4;
        }
        rc = sort_unique(&primary.nodes);
        if (rc == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            xp_live_frame frame;
            if (ctx->live != NULL) {
                frame = (xp_live_frame){.results = &primary, .result_count = 1};
                xp_live_enter(ctx, &frame);
            }
            rc = apply_predicates(prog, expr->second, ctx, &primary.nodes);
            xp_live_leave(ctx, &frame);
        }
        if (rc < 0) {
            xp_result_free(&primary);
            return rc;
        }
        *out = primary;
        return 0;
    }
    case XN_UNION: {
        xp_result left;
        xp_result right;
        int rc = eval_expr(prog, expr->first, ctx, &left);
        if (rc < 0) {
            return rc;
        }
        xp_live_frame frame;
        if (ctx->live != NULL) {
            frame = (xp_live_frame){.results = &left, .result_count = 1};
            xp_live_enter(ctx, &frame);
        }
        rc = eval_expr(prog, expr->second, ctx, &right);
        xp_live_leave(ctx, &frame);
        if (rc < 0) {
            xp_result_free(&left);
            return rc;
        }
        if (left.kind != XP_NODESET || right.kind != XP_NODESET) {
            xp_result_free(&left);
            xp_result_free(&right);
            *ctx->feature = "a union of non-node-sets";
            return -4;
        }
        if (nodeset_union(&left.nodes, &right.nodes) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            xp_result_free(&left);                          /* GCOVR_EXCL_LINE */
            xp_result_free(&right);                         /* GCOVR_EXCL_LINE: allocation failure */
            return -1;                                      /* GCOVR_EXCL_LINE */
        }
        *out = left;
        return 0;
    }
    case XN_OR:
    case XN_AND: {
        xp_result left;
        int rc = eval_expr(prog, expr->first, ctx, &left);
        if (rc < 0) {
            return rc;
        }
        int la = to_boolean(ctx->tree, &left);
        xp_result_free(&left);
        if (expr->kind == XN_OR ? la : !la) {
            result_bool(out, expr->kind == XN_OR);
            return 0;
        }
        xp_result right;
        rc = eval_expr(prog, expr->second, ctx, &right);
        if (rc < 0) {
            return rc;
        }
        result_bool(out, to_boolean(ctx->tree, &right));
        xp_result_free(&right);
        return 0;
    }
    case XN_NEG: {
        xp_result left;
        int rc = eval_expr(prog, expr->first, ctx, &left);
        if (rc < 0) {
            return rc;
        }
        result_number(out, -to_number(ctx->tree, &left));
        xp_result_free(&left);
        return 0;
    }
    case XN_FUNC:
        return eval_function(prog, idx, ctx, out);
    default: { /* the comparison and arithmetic binary operators */
        xp_result left;
        xp_result right;
        int rc = eval_expr(prog, expr->first, ctx, &left);
        if (rc < 0) {
            return rc;
        }
        xp_live_frame frame;
        if (ctx->live != NULL) {
            frame = (xp_live_frame){.results = &left, .result_count = 1};
            xp_live_enter(ctx, &frame);
        }
        rc = eval_expr(prog, expr->second, ctx, &right);
        xp_live_leave(ctx, &frame);
        if (rc < 0) {
            xp_result_free(&left);
            return rc;
        }
        if (expr->kind <= XN_GE) { /* the default case holds only the comparison and arithmetic operators */
            int cmp = 0;
            xp_live_frame comparison = {
                .sets = {left.kind == XP_NODESET ? &left.nodes : NULL, right.kind == XP_NODESET ? &right.nodes : NULL}};
            xp_live_enter(ctx, &comparison);
            rc = xp_before_python(ctx);
            if (rc == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                rc = compare(ctx->tree, expr->kind, &left, &right, &cmp);
            }
            xp_live_leave(ctx, &comparison);
            if (rc == 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                result_bool(out, cmp);
            }
        } else {
            double left_value = to_number(ctx->tree, &left);
            double right_value = to_number(ctx->tree, &right);
            double value = expr->kind == XN_ADD   ? left_value + right_value
                           : expr->kind == XN_SUB ? left_value - right_value
                           : expr->kind == XN_MUL ? left_value * right_value
                           : expr->kind == XN_DIV ? left_value / right_value
                                                  : fmod(left_value, right_value);
            result_number(out, value);
        }
        xp_result_free(&left);
        xp_result_free(&right);
        return rc;
    }
    }
}

/* Guard every recursive descent with a depth cap so a long operator spine or a deeply
   nested group raises past the bound instead of overflowing the C stack (issue #421). */
int eval_expr(const xp_program *prog, int32_t idx, xp_ctx *ctx, xp_result *out) {
    if (ctx->depth >= XP_MAX_DEPTH) {
        *ctx->feature = "an expression nested too deeply";
        return -3;
    }
    ctx->depth++;
    int rc;
    if (ctx->live == NULL) {
        rc = eval_expr_inner(prog, idx, ctx, out);
    } else {
        xp_live_frame frame = {.node = ctx->node};
        xp_live_enter(ctx, &frame);
        rc = eval_expr_inner(prog, idx, ctx, out);
        xp_live_leave(ctx, &frame);
    }
    ctx->depth--;
    return rc;
}

int xp_eval_at(const xp_program *prog, struct th_tree *tree, struct th_node *context, Py_ssize_t pos, Py_ssize_t size,
               const xp_bindings *vars, const xp_namespaces *namespaces, xp_extension_fn extension, void *extension_ctx,
               xp_result *out, const char **feature) {
    xr_cache *regex_cache = NULL;
    xp_ctx ctx = {tree,          context, -1,           pos,  size, feature, vars, namespaces, extension,
                  extension_ctx, 0,       &regex_cache, NULL, NULL, NULL,    NULL, 0};
    int rc = eval_expr(prog, prog->root, &ctx, out);
    if (regex_cache != NULL) {
        xr_cache_free(regex_cache);
    }
    return rc;
}

int xp_eval_pattern_at(const xp_program *prog, struct th_tree *tree, struct th_node *context, xp_extension_fn extension,
                       void *extension_ctx, xp_name_test_fn name_test, void *name_test_ctx, xp_result *out,
                       const char **feature) {
    xr_cache *regex_cache = NULL;
    xp_ctx ctx = {tree,
                  context,
                  -1,
                  1,
                  1,
                  feature,
                  NULL,
                  NULL,
                  extension,
                  extension_ctx,
                  0,
                  &regex_cache,
                  NULL,
                  NULL,
                  name_test,
                  name_test_ctx,
                  name_test == NULL && th_tree_is_xml(tree)}; /* GCOVR_EXCL_BR_LINE: XML only */
    int rc = eval_expr(prog, prog->root, &ctx, out);
    if (regex_cache != NULL) {
        xr_cache_free(regex_cache);
    }
    return rc;
}

int xp_eval(const xp_program *prog, struct th_tree *tree, struct th_node *context, const xp_bindings *vars,
            const xp_namespaces *namespaces, xp_extension_fn extension, void *extension_ctx, xp_result *out,
            const char **feature) {
    return xp_eval_at(prog, tree, context, 1, 1, vars, namespaces, extension, extension_ctx, out, feature);
}

int xp_eval_snapshot(const xp_program *prog, struct th_tree *tree, struct th_node *context, const xp_bindings *vars,
                     const xp_namespaces *namespaces, xp_extension_fn extension, void *extension_ctx,
                     xp_before_python_fn before_python, xp_result *out, const char **feature) {
    xr_cache *regex_cache = NULL;
    xp_live_registry live = {0};
    xp_ctx ctx = {
        tree,  context,       -1,   1,    1, feature, vars, namespaces, extension, extension_ctx, 0, &regex_cache,
        &live, before_python, NULL, NULL, 0};
    xp_live_frame frame = {.node = context, .vars = vars};
    xp_live_enter(&ctx, &frame);
    int rc = eval_expr(prog, prog->root, &ctx, out);
    if (regex_cache != NULL) {
        xr_cache_free(regex_cache);
    }
    return rc;
}
