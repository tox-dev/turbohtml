/* Canonical XML (c14n) serialization: the byte-exact form XML signatures sign.
   Renders a node and its complete subtree under Canonical XML 1.0/1.1 or Exclusive
   XML Canonicalization, with or without comments. The output is a UTF-8 infoset
   with normalized attribute order (namespace declarations first, then attributes
   sorted by namespace URI then local name), minimized namespace declarations
   (exclusive drops the ancestor namespaces a subtree does not visibly use), empty
   elements written as start-end pairs, and the c14n character-reference rules.

   turbohtml canonicalizes a complete subtree (a node and every descendant), never a
   filtered node-set, so c14n 1.1 fixes the apex's inherited xml:base and excludes
   xml:id and arbitrary xml: attributes from simple inheritance. The namespace
   axis this walks is turbohtml's HTML infoset -- HTML elements carry no namespace,
   SVG and MathML carry their default namespace, and an xlink: attribute binds the
   xlink prefix on the element that uses it -- which is exactly what the XML
   serialization emits, so the output matches lxml/libxml2 reparsing that form. */

#include "serialize/internal.h"
#include "serialize/c14n_base.h"

#include "core/work_steps.h"
#include "dom/tree.h"
#include "dom/tree_internal.h"

#include <stdlib.h>
#include <string.h>

static const char C14N_SVG[] = "http://www.w3.org/2000/svg";
static const char C14N_MATHML[] = "http://www.w3.org/1998/Math/MathML";
static const char C14N_XLINK[] = "http://www.w3.org/1999/xlink";
static const char C14N_XML[] = "http://www.w3.org/XML/1998/namespace";

static int c14n_1_1_inherits_xml_attribute(const char *name, Py_ssize_t len) {
    return (len == 8 && (memcmp(name + 4, "lang", 4) == 0 || memcmp(name + 4, "base", 4) == 0)) ||
           (len == 9 && memcmp(name + 4, "space", 5) == 0);
}

/* Append c14n text: only & < > and a carriage return take a reference (as &amp;
   &lt; &gt; &#xD;); tab and newline stay literal, unlike an attribute value. Each
   clean run is bulk-copied and only the specials between are rewritten. A character
   XML cannot hold stops the run and reaches no branch, so it is dropped. */
static void c14n_put_text(sbuf *out, const Py_UCS4 *text, Py_ssize_t len) {
    Py_ssize_t index = 0;
    while (index < len) {
        Py_ssize_t start = index;
        index = c14n_find_text_stop(text, index, len);
        if (index > start) {
            sbuf_put_run(out, &text[start], index - start);
        }
        if (index < len) {
            Py_UCS4 character = text[index];
            if (character == '&') {
                sbuf_puts(out, "&amp;");
            } else if (character == '<') {
                sbuf_puts(out, "&lt;");
            } else if (character == '>') {
                sbuf_puts(out, "&gt;");
            } else if (character == 0x0D) {
                sbuf_puts(out, "&#xD;");
            }
            index++;
        }
    }
}

/* Append a c14n attribute value: & < " take references, and the three whitespace
   controls tab/newline/carriage-return become &#x9; &#xA; &#xD; so a reparse cannot
   normalize them away; `>` and `'` stay literal. A character XML cannot hold stops
   the run and reaches no branch, so it is dropped. */
static void c14n_put_attr(sbuf *out, const Py_UCS4 *text, Py_ssize_t len) {
    Py_ssize_t index = 0;
    while (index < len) {
        Py_ssize_t start = index;
        index = c14n_find_stop(text, index, len, C14N_ATTR_STOPS);
        if (index > start) {
            sbuf_put_run(out, &text[start], index - start);
        }
        if (index < len) {
            Py_UCS4 character = text[index];
            if (character == '&') {
                sbuf_puts(out, "&amp;");
            } else if (character == '<') {
                sbuf_puts(out, "&lt;");
            } else if (character == '"') {
                sbuf_puts(out, "&quot;");
            } else if (character == 0x09) {
                sbuf_puts(out, "&#x9;");
            } else if (character == 0x0A) {
                sbuf_puts(out, "&#xA;");
            } else if (character == 0x0D) {
                sbuf_puts(out, "&#xD;");
            }
            index++;
        }
    }
}

/* The default-namespace URI an element declares by virtue of its namespace, keyed by
   the th_ns enum: SVG and MathML their own, an HTML element none (the empty string,
   which undeclares an inherited default). A branchless table keeps coverage exact
   when the compiler inlines this into the start-tag writer. */
static const char *c14n_default_uri(uint8_t ns) {
    static const char *const URIS[] = {"", C14N_SVG, C14N_MATHML};
    return URIS[ns];
}

static int c14n_name_is_xlink(const char *name, Py_ssize_t len) {
    return len > 6 && memcmp(name, "xlink:", 6) == 0;
}

/* Whether an element carries any xlink:-prefixed attribute, which is what binds the
   xlink prefix on it in turbohtml's infoset. A non-element (a document or content
   ancestor on the scope walk) binds nothing. */
static int c14n_has_xlink(th_tree *tree, const th_node *node) {
    if (node->type != TH_NODE_ELEMENT) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, node->attrs[index].name_atom, &name_len);
        if (c14n_name_is_xlink(name, name_len)) {
            return 1;
        }
    }
    return 0;
}

/* Whether the xlink prefix is in scope at node: bound by node or any ancestor. */
static int c14n_xlink_in_scope(th_tree *tree, const th_node *node) {
    for (const th_node *scan = node; scan != NULL; scan = scan->parent) {
        if (c14n_has_xlink(tree, scan)) {
            return 1;
        }
    }
    return 0;
}

static int c14n_prefix_forced(const th_c14n_opts *opts, const char *prefix) {
    int forced = 0;
    for (Py_ssize_t index = 0; index < opts->inclusive_count && !forced; index++) {
        forced = strcmp(opts->inclusive[index], prefix) == 0;
    }
    return forced;
}

/* One attribute queued for the sorted emission, resolved to the (namespace URI,
   local name) key c14n orders on and holding the qualified name and value to write. */
typedef struct {
    const char *name;
    Py_ssize_t name_len;
    const char *ns;
    Py_ssize_t ns_len;
    const char *local;
    Py_ssize_t local_len;
    const Py_UCS4 *value;
    Py_ssize_t value_len;
    Py_ssize_t position; /* order gathered: the element's own attributes, then the nearest ancestor's first */
} c14n_attr;

/* Resolve a stored attribute name/value to its c14n sort key: an xml:/xlink: prefix
   maps to its namespace URI and strips to the local name, everything else is in no
   namespace and keys on the whole name. */
static c14n_attr c14n_make_ref(const char *name, Py_ssize_t name_len, const Py_UCS4 *value, Py_ssize_t value_len) {
    c14n_attr ref = {name, name_len, "", 0, name, name_len, value, value_len, 0};
    if (name_len > 4 && memcmp(name, "xml:", 4) == 0) {
        ref.ns = C14N_XML;
        ref.ns_len = (Py_ssize_t)sizeof(C14N_XML) - 1;
        ref.local = name + 4;
        ref.local_len = name_len - 4;
    } else if (c14n_name_is_xlink(name, name_len)) {
        ref.ns = C14N_XLINK;
        ref.ns_len = (Py_ssize_t)sizeof(C14N_XLINK) - 1;
        ref.local = name + 6;
        ref.local_len = name_len - 6;
    }
    return ref;
}

static int c14n_key_cmp(const c14n_attr *left, const c14n_attr *right) {
    Py_ssize_t min_ns = left->ns_len < right->ns_len ? left->ns_len : right->ns_len;
    int order = memcmp(left->ns, right->ns, (size_t)min_ns);
    if (order != 0) {
        return order;
    }
    if (left->ns_len != right->ns_len) {
        return left->ns_len < right->ns_len ? -1 : 1;
    }
    Py_ssize_t min_local = left->local_len < right->local_len ? left->local_len : right->local_len;
    order = memcmp(left->local, right->local, (size_t)min_local);
    if (order != 0) {
        return order;
    }
    return (left->local_len > right->local_len) - (left->local_len < right->local_len);
}

static int c14n_attr_cmp(const void *left, const void *right) {
    return c14n_key_cmp(left, right);
}

/* A stored xmlns / xmlns:* attribute is a namespace declaration, emitted from the
   namespace axis rather than as a plain attribute, so it is filtered from the
   attribute list. */
static int c14n_name_is_nsdecl(const char *name, Py_ssize_t len) {
    return (len == 5 && memcmp(name, "xmlns", 5) == 0) || (len > 6 && memcmp(name, "xmlns:", 6) == 0);
}

/* By qualified name and then by the order gathered, so the attribute that wins a
   name leads the ones it shadows. */
static int c14n_attr_name_cmp(const void *left, const void *right) {
    const c14n_attr *first = left;
    const c14n_attr *second = right;
    TH_WORK_STEP();
    Py_ssize_t common = first->name_len < second->name_len ? first->name_len : second->name_len;
    int order = memcmp(first->name, second->name, (size_t)common);
    if (order == 0) {
        order = (first->name_len > second->name_len) - (first->name_len < second->name_len);
    }
    return order != 0 ? order : (first->position > second->position) - (first->position < second->position);
}

/* Drop each inherited xml: attribute that the apex or a nearer ancestor already
   supplies, so it never shadows theirs. attrs holds the apex's own attributes,
   which all stay, and then every inheritable one, nearest ancestor first. Looking
   each one up among those gathered before it costs the square of their number, so
   they are sorted by name instead and only the first of a name is kept. */
static Py_ssize_t c14n_drop_shadowed(c14n_attr *attrs, Py_ssize_t own, Py_ssize_t count) {
    for (Py_ssize_t index = 0; index < count; index++) {
        attrs[index].position = index;
    }
    qsort(attrs, (size_t)count, sizeof(*attrs), c14n_attr_name_cmp);
    Py_ssize_t kept = 1;
    for (Py_ssize_t index = 1; index < count; index++) {
        if (attrs[index].position < own || attrs[index].name_len != attrs[index - 1].name_len ||
            memcmp(attrs[index].name, attrs[index - 1].name, (size_t)attrs[index].name_len) != 0) {
            attrs[kept++] = attrs[index];
        }
    }
    return kept;
}

#define C14N_STACK_ATTRS 32

/* An element whose name XML cannot spell, which the canonical form leaves out as serialize(xml) does, while the walk
   is inside it: the default namespace its children are written against, whether they stand in for the apex, and, for
   the walk of a tree with prefixed declarations, where the declarations of the left-out run it ends start. */
typedef struct {
    const th_node *node;
    uint8_t ns;
    int apex;
    Py_ssize_t run;
} c14n_left_out;

/* The left-out elements the walk is inside, innermost last. */
typedef struct {
    c14n_left_out *items;
    Py_ssize_t len, cap;
} c14n_left_outs;

static const c14n_left_out *c14n_left_out_of(const c14n_left_outs *left_outs, const th_node *element) {
    return left_outs->len > 0 && left_outs->items[left_outs->len - 1].node == element
               ? &left_outs->items[left_outs->len - 1]
               : NULL;
}

/* Whether node is written as an apex: the apex itself, or a child standing in for a left-out apex. */
static int c14n_is_apex(const th_node *node, const th_node *apex, const c14n_left_out *left_out) {
    return node == apex || (left_out != NULL && left_out->apex);
}

/* The default namespace of node's output parent: the empty default above the subset for an apex, else that of the
   left-out element's tag node stands in for, else its parent's. */
static uint8_t c14n_parent_ns(const th_node *node, int is_apex, const c14n_left_out *left_out) {
    if (is_apex) {
        return TH_NS_HTML;
    }
    if (left_out != NULL) {
        return left_out->ns;
    }
    return node->parent->type == TH_NODE_ELEMENT ? node->parent->ns : TH_NS_HTML;
}

/* Emit an element's start tag "<name decls attrs>": the namespace declarations the
   c14n namespace axis renders (default namespace, then the xlink prefix), followed
   by the attributes in (namespace URI, local name) order. When node is written as an
   apex its ancestors' xml: attributes are inherited onto it (c14n 1.1 excludes xml:id). */
static TH_TREE_INLINE void c14n_open_tag_as(sbuf *out, th_tree *tree, th_node *node, int is_apex, uint8_t parent_ns,
                                            const th_c14n_opts *opts, const th_node **xlink_binding) {
    sbuf_putc(out, '<');
    sbuf_put_ucs4(out, node->text, node->text_len);

    /* the default namespace is rendered when it differs from the output parent's;
       comparing the ns enum covers this exactly, HTML being the empty default */
    if (node->ns != parent_ns) {
        sbuf_puts(out, " xmlns=\"");
        sbuf_puts(out, c14n_default_uri(node->ns));
        sbuf_putc(out, '"');
    }

    int has_xlink = c14n_has_xlink(tree, node);
    int render_xlink;
    if (is_apex) {
        if (opts->exclusive) {
            render_xlink = has_xlink || (c14n_prefix_forced(opts, "xlink") && c14n_xlink_in_scope(tree, node));
        } else {
            render_xlink = has_xlink || *xlink_binding != NULL;
        }
    } else {
        render_xlink = has_xlink && *xlink_binding == NULL;
    }
    if (has_xlink && *xlink_binding == NULL) {
        *xlink_binding = node;
    }
    if (render_xlink) {
        sbuf_puts(out, " xmlns:xlink=\"");
        sbuf_puts(out, C14N_XLINK);
        sbuf_putc(out, '"');
    }

    Py_ssize_t capacity = node->attr_count;
    if (is_apex && !opts->exclusive) {
        for (const th_node *anc = node->parent; anc != NULL; anc = anc->parent) {
            if (anc->type == TH_NODE_ELEMENT) {
                capacity += anc->attr_count;
            }
        }
    }
    c14n_attr stack_attrs[C14N_STACK_ATTRS];
    c14n_attr *attrs = capacity <= C14N_STACK_ATTRS ? stack_attrs : PyMem_Malloc((size_t)capacity * sizeof(c14n_attr));
    if (attrs == NULL) { /* GCOVR_EXCL_BR_LINE: only the heap path can fail, and OOM is unforceable */
        return;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t count = 0;
    int needs_base_fixup = 0;
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, node->attrs[index].name_atom, &name_len);
        if (c14n_name_is_nsdecl(name, name_len)) {
            continue;
        }
        attrs[count++] = c14n_make_ref(name, name_len, node->attrs[index].value, node->attrs[index].value_len);
    }
    if (is_apex && !opts->exclusive) {
        Py_ssize_t own = count;
        for (const th_node *anc = node->parent; anc != NULL; anc = anc->parent) {
            if (anc->type != TH_NODE_ELEMENT) {
                continue;
            }
            for (Py_ssize_t index = 0; index < anc->attr_count; index++) {
                Py_ssize_t name_len;
                const char *name = th_attr_name(tree, anc->attrs[index].name_atom, &name_len);
                needs_base_fixup |= name_len == 8 && memcmp(name, "xml:base", 8) == 0;
                if (name_len <= 4 || memcmp(name, "xml:", 4) != 0) {
                    continue;
                }
                if (opts->version == 1 && !c14n_1_1_inherits_xml_attribute(name, name_len)) {
                    continue;
                }
                attrs[count++] = c14n_make_ref(name, name_len, anc->attrs[index].value, anc->attrs[index].value_len);
            }
        }
        if (count > own) {
            count = c14n_drop_shadowed(attrs, own, count);
        }
    }
    Py_UCS4 *fixed_base = NULL;
    if (is_apex && opts->version == 1 && needs_base_fixup) {
        Py_ssize_t base_len;
        if (c14n_base_fixup(tree, node, &fixed_base, &base_len) < 0) {
            if (attrs != stack_attrs) {
                PyMem_Free(attrs);
            }
            out->failed = 1;
            return;
        }
        Py_ssize_t base_index = 0;
        while (attrs[base_index].name_len != 8 || memcmp(attrs[base_index].name, "xml:base", 8) != 0) {
            base_index++;
        }
        if (base_len == 0) {
            attrs[base_index] = attrs[--count];
        } else {
            attrs[base_index].value = fixed_base;
            attrs[base_index].value_len = base_len;
        }
    }
    if (count > 32) {
        qsort(attrs, (size_t)count, sizeof(*attrs), c14n_attr_cmp);
    } else {
        for (Py_ssize_t index = 1; index < count; index++) {
            const c14n_attr key = attrs[index];
            Py_ssize_t prev = index - 1;
            while (prev >= 0 && c14n_key_cmp(&attrs[prev], &key) > 0) {
                attrs[prev + 1] = attrs[prev];
                prev--;
            }
            attrs[prev + 1] = key;
        }
    }
    Py_ssize_t clone_start = ser_clone_start(out, node);
    for (Py_ssize_t index = 0; index < count; index++) {
        Py_ssize_t cost = attrs[index].name_len + attrs[index].value_len;
        if (clone_start >= 0 && !ser_clone_attr_fits(out, clone_start, cost)) {
            break;
        }
        TH_WORK_STEPS_ADD(cost);
        sbuf_putc(out, ' ');
        sbuf_put_utf8(out, attrs[index].name, attrs[index].name_len);
        sbuf_puts(out, "=\"");
        c14n_put_attr(out, attrs[index].value, attrs[index].value_len);
        sbuf_putc(out, '"');
    }
    ser_clone_settle(out, clone_start);
    if (attrs != stack_attrs) {
        PyMem_Free(attrs);
    }
    if (fixed_base != NULL) {
        PyMem_Free(fixed_base);
    }
    sbuf_putc(out, '>');
}

/* The start tag of node in a subtree without left-out elements, an apex rendering against the empty default above
   the subset. */
static void c14n_open_tag(sbuf *out, th_tree *tree, th_node *node, const th_node *apex, const th_c14n_opts *opts,
                          const th_node **xlink_binding) {
    uint8_t parent_ns = TH_NS_HTML;
    if (node != apex && node->parent->type == TH_NODE_ELEMENT) {
        parent_ns = node->parent->ns;
    }
    c14n_open_tag_as(out, tree, node, node == apex, parent_ns, opts, xlink_binding);
}

#if defined(_MSC_VER)
#define C14N_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define C14N_NOINLINE __attribute__((noinline))
#else
#define C14N_NOINLINE
#endif

/* The start tag of node in a walk leaving elements out: a child of a left-out element (left_out, NULL otherwise) is
   written against what that element's tag would have been. Kept out of line so the walk without left-out elements
   carries no copy of it. */
static C14N_NOINLINE void c14n_left_out_open_tag(sbuf *out, th_tree *tree, th_node *node, const th_node *apex,
                                                 const c14n_left_out *left_out, const th_c14n_opts *opts,
                                                 const th_node **xlink_binding) {
    int is_apex = c14n_is_apex(node, apex, left_out);
    c14n_open_tag_as(out, tree, node, is_apex, c14n_parent_ns(node, is_apex, left_out), opts, xlink_binding);
}

static void c14n_close_tag(sbuf *out, const th_node *node) {
    sbuf_puts(out, "</");
    sbuf_put_ucs4(out, node->text, node->text_len);
    sbuf_putc(out, '>');
}

static void c14n_put_pi(sbuf *out, const th_node *node) {
    if (node->tag_flags & TH_PI_XML_INVALID) {
        return;
    }
    Py_ssize_t target_len = node->attr_count;
    sbuf_puts(out, "<?");
    sbuf_put_ucs4(out, node->text, target_len);
    Py_ssize_t data_len = node->text_len - target_len - 1;
    if (data_len > 0) {
        sbuf_putc(out, ' ');
        sbuf_put_xml_pi_data(out, node->text + target_len + 1, data_len);
    }
    sbuf_puts(out, "?>");
}

/* Enter node, which the canonical form leaves out, its children standing in for it; left_out is the left-out element
   node is a child of, or NULL. */
static int c14n_left_out_push(c14n_left_outs *left_outs, const th_node *node, const th_node *apex,
                              const c14n_left_out *left_out, Py_ssize_t run) {
    int is_apex = c14n_is_apex(node, apex, left_out);
    uint8_t ns = c14n_parent_ns(node, is_apex, left_out);
    c14n_left_out *items = ser_reserve(left_outs->items, left_outs->len, &left_outs->cap, sizeof(*items));
    if (items == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    left_outs->items = items;
    left_outs->items[left_outs->len++] = (c14n_left_out){node, ns, is_apex, run};
    return 0;
}

static void c14n_walk_leaving_out(sbuf *out, th_tree *tree, th_node *node, const th_node *apex,
                                  const th_c14n_opts *opts, const th_node **xlink_binding);

/* Emit one node of the subtree rooted at apex and return the next node the walk
   visits, or NULL once the subtree is done. Iterative -- descending through
   first_child and closing each element on the way back up -- so an arbitrarily deep
   tree serializes without one C stack frame per level. left_outs is NULL until the
   walk meets an element it leaves out, which hands the rest of the subtree to
   c14n_walk_leaving_out, so a subtree without one pays no left-out bookkeeping. */
static TH_TREE_INLINE th_node *c14n_step(sbuf *out, th_tree *tree, th_node *node, const th_node *apex,
                                         const th_c14n_opts *opts, const th_node **xlink_binding,
                                         c14n_left_outs *left_outs) {
    th_node *descend = NULL;
    switch ((enum th_node_type)node->type) { /* GCOVR_EXCL_BR_LINE: node types are exhaustive */
    case TH_NODE_ELEMENT: {
        if (left_outs == NULL) {
            if (node->atom == TH_TAG_UNKNOWN && !ser_xml_element_name_wellformed(node)) {
                c14n_walk_leaving_out(out, tree, node, apex, opts, xlink_binding);
                return NULL;
            }
            c14n_open_tag(out, tree, node, apex, opts, xlink_binding);
        } else {
            const c14n_left_out *left_out = c14n_left_out_of(left_outs, node->parent);
            if (node->atom == TH_TAG_UNKNOWN && !ser_xml_element_name_wellformed(node)) {
                if (node->first_child == NULL) {
                    break;
                }
                if (c14n_left_out_push(left_outs, node, apex, left_out, 0) < 0) { /* GCOVR_EXCL_BR_LINE: OOM */
                    out->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
                    return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
                }
                descend = node->first_child;
                break;
            }
            c14n_left_out_open_tag(out, tree, node, apex, left_out, opts, xlink_binding);
        }
        if (node->first_child != NULL) {
            descend = node->first_child;
        } else {
            c14n_close_tag(out, node); /* an empty element is a start-end pair, never self-closed */
        }
        break;
    }
    case TH_NODE_TEXT:
    case TH_NODE_CDATA: /* a CDATA section canonicalizes as its escaped character data */
        if (need_text(tree, node) == NULL && node->text_len > 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
            out->failed = 1;                                       /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;                                           /* GCOVR_EXCL_LINE: allocation failure */
        }
        c14n_put_text(out, node->text, node->text_len);
        break;
    case TH_NODE_COMMENT:
        if (opts->with_comments) {
            sbuf_puts(out, "<!--");
            sbuf_put_xml_comment(out, node->text, node->text_len);
            sbuf_puts(out, "-->");
        }
        break;
    case TH_NODE_PI:
        c14n_put_pi(out, node);
        break;
    case TH_NODE_DOCTYPE: /* the document type declaration is dropped from the canonical form */
        break;
    case TH_NODE_CONTENT:
    case TH_NODE_DOCUMENT:
        descend = node->first_child; /* a transparent container emits only its children */
        break;
    }
    if (descend != NULL) {
        return descend;
    }
    while (node != apex) {
        if (*xlink_binding == node) {
            *xlink_binding = NULL;
        }
        if (node->next_sibling != NULL) {
            return node->next_sibling;
        }
        node = node->parent;
        if (left_outs != NULL && c14n_left_out_of(left_outs, node) != NULL) {
            left_outs->len--;
        } else if (node->type == TH_NODE_ELEMENT) {
            c14n_close_tag(out, node);
        }
    }
    return NULL;
}

static void c14n_subtree(sbuf *out, th_tree *tree, th_node *apex, const th_c14n_opts *opts) {
    /* Only the first actual binding needs a lifetime; nested bindings cannot outlive it. */
    const th_node *xlink_binding = !opts->exclusive && c14n_xlink_in_scope(tree, apex->parent) ? apex->parent : NULL;
    th_node *node = apex;
    while (node != NULL) {
        node = c14n_step(out, tree, node, apex, opts, &xlink_binding, NULL);
    }
}

/* Finish the walk of the subtree rooted at apex from node, the first element it leaves out, tracking the left-out
   elements it is inside. */
static C14N_NOINLINE void c14n_walk_leaving_out(sbuf *out, th_tree *tree, th_node *node, const th_node *apex,
                                                const th_c14n_opts *opts, const th_node **xlink_binding) {
    c14n_left_outs left_outs = {NULL, 0, 0};
    while (node != NULL) {
        node = c14n_step(out, tree, node, apex, opts, xlink_binding, &left_outs);
    }
    PyMem_Free(left_outs.items);
}

/* Canonicalize a document node: each element child as its own apex, and every
   comment (when kept) or processing instruction outside the document element set
   off with a newline -- after the node before the root, before the node after it. */
static void c14n_document(sbuf *out, th_tree *tree, th_node *doc, const th_c14n_opts *opts) {
    int seen_root = 0;
    for (th_node *child = doc->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT) {
            c14n_subtree(out, tree, child, opts);
            seen_root = 1;
        } else if (child->type == TH_NODE_COMMENT && opts->with_comments) {
            if (seen_root) {
                sbuf_putc(out, '\n');
            }
            sbuf_puts(out, "<!--");
            sbuf_put_xml_comment(out, child->text, child->text_len);
            sbuf_puts(out, "-->");
            if (!seen_root) {
                sbuf_putc(out, '\n');
            }
        } else if (child->type == TH_NODE_PI) {
            if (seen_root) {
                sbuf_putc(out, '\n');
            }
            c14n_put_pi(out, child);
            if (!seen_root) {
                sbuf_putc(out, '\n');
            }
        }
    }
}

#include "serialize/c14n_scoped.h"

Py_UCS4 *th_node_canonicalize(th_tree *tree, th_node *node, const th_c14n_opts *opts, Py_ssize_t *out_len) {
    if (tree->has_xml_ns) {
        return th_node_canonicalize_xml(tree, node, opts, out_len);
    }
    if (tree->has_prefix_decl) {
        return c14n_scoped_canonicalize(tree, node, opts, out_len);
    }
    sbuf out = {NULL, 0, 0, 0, 0};
    if (node->type == TH_NODE_DOCUMENT) {
        sbuf_presize_for_c14n(&out, tree);
        c14n_document(&out, tree, node, opts);
    } else {
        c14n_subtree(&out, tree, node, opts);
    }
    return sbuf_finish(&out, out_len);
}
