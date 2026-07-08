/* Canonical XML (c14n) serialization: the byte-exact form XML signatures sign.
   Renders a node and its complete subtree under Canonical XML 1.0/1.1 or Exclusive
   XML Canonicalization, with or without comments. The output is a UTF-8 infoset
   with normalized attribute order (namespace declarations first, then attributes
   sorted by namespace URI then local name), minimized namespace declarations
   (exclusive drops the ancestor namespaces a subtree does not visibly use), empty
   elements written as start-end pairs, and the c14n character-reference rules.

   turbohtml canonicalizes a complete subtree (a node and every descendant), never a
   filtered node-set, so the only place c14n 1.1 diverges from 1.0 here is the apex's
   inherited xml: attributes: 1.1 excludes xml:id from that inheritance. The namespace
   axis this walks is turbohtml's HTML infoset -- HTML elements carry no namespace,
   SVG and MathML carry their default namespace, and an xlink: attribute binds the
   xlink prefix on the element that uses it -- which is exactly what the XML
   serialization emits, so the output matches lxml/libxml2 reparsing that form. */

#include "serialize/internal.h"

#include "dom/tree.h"
#include "dom/tree_internal.h"

#include <string.h>

static const char C14N_SVG[] = "http://www.w3.org/2000/svg";
static const char C14N_MATHML[] = "http://www.w3.org/1998/Math/MathML";
static const char C14N_XLINK[] = "http://www.w3.org/1999/xlink";
static const char C14N_XML[] = "http://www.w3.org/XML/1998/namespace";

/* Append c14n text: only & < > and a carriage return take a reference (as &amp;
   &lt; &gt; &#xD;); tab and newline stay literal, unlike an attribute value. Each
   clean run is bulk-copied and only the specials between are rewritten. */
static void c14n_put_text(sbuf *out, const Py_UCS4 *text, Py_ssize_t len) {
    Py_ssize_t index = 0;
    while (index < len) {
        Py_ssize_t start = index;
        while (index < len && text[index] != '&' && text[index] != '<' && text[index] != '>' && text[index] != 0x0D) {
            index++;
        }
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
            } else {
                sbuf_puts(out, "&#xD;"); /* the only remaining flagged character is U+000D */
            }
            index++;
        }
    }
}

/* Append a c14n attribute value: & < " take references, and the three whitespace
   controls tab/newline/carriage-return become &#x9; &#xA; &#xD; so a reparse cannot
   normalize them away; `>` and `'` stay literal. */
static void c14n_put_attr(sbuf *out, const Py_UCS4 *text, Py_ssize_t len) {
    Py_ssize_t index = 0;
    while (index < len) {
        Py_ssize_t start = index;
        while (index < len && text[index] != '&' && text[index] != '<' && text[index] != '"' && text[index] != 0x09 &&
               text[index] != 0x0A && text[index] != 0x0D) {
            index++;
        }
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
            } else {
                sbuf_puts(out, "&#xD;"); /* the only remaining flagged character is U+000D */
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

/* Whether a proper ancestor of node up to and including apex binds the xlink prefix
   -- the exclusive-c14n test for "already rendered by an output ancestor". apex is
   always an ancestor of node here, so the walk always reaches it. */
static int c14n_ancestor_binds_xlink(th_tree *tree, const th_node *node, const th_node *apex) {
    for (const th_node *scan = node->parent;; scan = scan->parent) {
        if (c14n_has_xlink(tree, scan)) {
            return 1;
        }
        if (scan == apex) {
            return 0;
        }
    }
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
} c14n_attr;

/* Resolve a stored attribute name/value to its c14n sort key: an xml:/xlink: prefix
   maps to its namespace URI and strips to the local name, everything else is in no
   namespace and keys on the whole name. */
static c14n_attr c14n_make_ref(const char *name, Py_ssize_t name_len, const Py_UCS4 *value, Py_ssize_t value_len) {
    c14n_attr ref = {name, name_len, "", 0, name, name_len, value, value_len};
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

/* Order two attributes by (namespace URI, local name). Distinct attributes never tie
   on both, so a shared prefix falls to the length comparison; equal length there is
   unreachable and the arbitrary 1 it would return never affects the sort. */
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
    return left->local_len < right->local_len ? -1 : 1;
}

/* A stored xmlns / xmlns:* attribute is a namespace declaration, emitted from the
   namespace axis rather than as a plain attribute, so it is filtered from the
   attribute list. */
static int c14n_name_is_nsdecl(const char *name, Py_ssize_t len) {
    return (len == 5 && memcmp(name, "xmlns", 5) == 0) || (len > 6 && memcmp(name, "xmlns:", 6) == 0);
}

/* Whether a queued attribute with this qualified name is already collected, so an
   inherited xml: attribute never shadows the apex's own or a nearer ancestor's. */
static int c14n_already_has(const c14n_attr *attrs, Py_ssize_t count, const char *name, Py_ssize_t name_len) {
    for (Py_ssize_t index = 0; index < count; index++) {
        if (attrs[index].name_len == name_len && memcmp(attrs[index].name, name, (size_t)name_len) == 0) {
            return 1;
        }
    }
    return 0;
}

#define C14N_STACK_ATTRS 32

/* Emit an element's start tag "<name decls attrs>": the namespace declarations the
   c14n namespace axis renders (default namespace, then the xlink prefix), followed
   by the attributes in (namespace URI, local name) order. When node is the apex its
   ancestors' xml: attributes are inherited onto it (c14n 1.1 excludes xml:id). */
static void c14n_open_tag(sbuf *out, th_tree *tree, th_node *node, const th_node *apex, const th_c14n_opts *opts) {
    sbuf_putc(out, '<');
    sbuf_put_ucs4(out, node->text, node->text_len);

    /* the default namespace is rendered when it differs from the output parent's;
       comparing the ns enum covers this exactly (HTML is the empty default) and, for
       an apex, treats the context above the subset as the empty default */
    uint8_t parent_ns = TH_NS_HTML;
    if (node != apex && node->parent->type == TH_NODE_ELEMENT) {
        parent_ns = node->parent->ns;
    }
    if (node->ns != parent_ns) {
        sbuf_puts(out, " xmlns=\"");
        sbuf_puts(out, c14n_default_uri(node->ns));
        sbuf_putc(out, '"');
    }

    int render_xlink;
    if (node == apex) {
        if (opts->exclusive) {
            render_xlink =
                c14n_has_xlink(tree, node) || (c14n_prefix_forced(opts, "xlink") && c14n_xlink_in_scope(tree, node));
        } else {
            render_xlink = c14n_xlink_in_scope(tree, node);
        }
    } else if (opts->exclusive) {
        render_xlink = c14n_has_xlink(tree, node) && !c14n_ancestor_binds_xlink(tree, node, apex);
    } else {
        render_xlink = c14n_has_xlink(tree, node) && !c14n_xlink_in_scope(tree, node->parent);
    }
    if (render_xlink) {
        sbuf_puts(out, " xmlns:xlink=\"");
        sbuf_puts(out, C14N_XLINK);
        sbuf_putc(out, '"');
    }

    Py_ssize_t capacity = node->attr_count;
    if (node == apex) {
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
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, node->attrs[index].name_atom, &name_len);
        if (c14n_name_is_nsdecl(name, name_len)) {
            continue;
        }
        attrs[count++] = c14n_make_ref(name, name_len, node->attrs[index].value, node->attrs[index].value_len);
    }
    if (node == apex) {
        /* the apex of the subset inherits its ancestors' xml: attributes, nearest
           value winning; c14n 1.1 keeps xml:id local to avoid duplicating an id */
        for (const th_node *anc = node->parent; anc != NULL; anc = anc->parent) {
            if (anc->type != TH_NODE_ELEMENT) {
                continue;
            }
            for (Py_ssize_t index = 0; index < anc->attr_count; index++) {
                Py_ssize_t name_len;
                const char *name = th_attr_name(tree, anc->attrs[index].name_atom, &name_len);
                if (name_len <= 4 || memcmp(name, "xml:", 4) != 0) {
                    continue;
                }
                if (opts->version == 1 && name_len == 6 && memcmp(name, "xml:id", 6) == 0) {
                    continue;
                }
                if (!c14n_already_has(attrs, count, name, name_len)) {
                    attrs[count++] =
                        c14n_make_ref(name, name_len, anc->attrs[index].value, anc->attrs[index].value_len);
                }
            }
        }
    }
    for (Py_ssize_t index = 1; index < count; index++) { /* insertion sort; attribute counts are tiny */
        c14n_attr key = attrs[index];
        Py_ssize_t prev = index - 1;
        while (prev >= 0 && c14n_key_cmp(&attrs[prev], &key) > 0) {
            attrs[prev + 1] = attrs[prev];
            prev--;
        }
        attrs[prev + 1] = key;
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        sbuf_putc(out, ' ');
        sbuf_put_utf8(out, attrs[index].name, attrs[index].name_len);
        sbuf_puts(out, "=\"");
        c14n_put_attr(out, attrs[index].value, attrs[index].value_len);
        sbuf_putc(out, '"');
    }
    if (attrs != stack_attrs) {
        PyMem_Free(attrs);
    }
    sbuf_putc(out, '>');
}

static void c14n_close_tag(sbuf *out, const th_node *node) {
    sbuf_puts(out, "</");
    sbuf_put_ucs4(out, node->text, node->text_len);
    sbuf_putc(out, '>');
}

static void c14n_put_pi(sbuf *out, const th_node *node) {
    Py_ssize_t target_len = node->attr_count;
    sbuf_puts(out, "<?");
    sbuf_put_ucs4(out, node->text, target_len);
    Py_ssize_t data_len = node->text_len - target_len - 1;
    if (data_len > 0) {
        sbuf_putc(out, ' ');
        sbuf_put_ucs4(out, node->text + target_len + 1, data_len);
    }
    sbuf_puts(out, "?>");
}

/* Emit one node of the subtree rooted at apex and return the next node the walk
   visits, or NULL once the subtree is done. Iterative -- descending through
   first_child and closing each element on the way back up -- so an arbitrarily deep
   tree serializes without one C stack frame per level. */
static th_node *c14n_step(sbuf *out, th_tree *tree, th_node *node, const th_node *apex, const th_c14n_opts *opts) {
    th_node *descend = NULL;
    switch (node->type) { /* GCOVR_EXCL_BR_LINE: th_node_type is exhaustive; the implicit default is unreachable */
    case TH_NODE_ELEMENT:
        c14n_open_tag(out, tree, node, apex, opts);
        if (node->first_child != NULL) {
            descend = node->first_child;
        } else {
            c14n_close_tag(out, node); /* an empty element is a start-end pair, never self-closed */
        }
        break;
    case TH_NODE_TEXT:
    case TH_NODE_CDATA: /* a CDATA section canonicalizes as its escaped character data */
        c14n_put_text(out, need_text(tree, node), node->text_len);
        break;
    case TH_NODE_COMMENT:
        if (opts->with_comments) {
            sbuf_puts(out, "<!--");
            sbuf_put_ucs4(out, node->text, node->text_len);
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
        if (node->next_sibling != NULL) {
            return node->next_sibling;
        }
        node = node->parent;
        if (node->type == TH_NODE_ELEMENT) {
            c14n_close_tag(out, node);
        }
    }
    return NULL;
}

static void c14n_subtree(sbuf *out, th_tree *tree, th_node *apex, const th_c14n_opts *opts) {
    th_node *node = apex;
    while (node != NULL) {
        node = c14n_step(out, tree, node, apex, opts);
    }
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
            sbuf_put_ucs4(out, child->text, child->text_len);
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

Py_UCS4 *th_node_canonicalize(th_tree *tree, th_node *node, const th_c14n_opts *opts, Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0};
    if (node->type == TH_NODE_DOCUMENT) {
        c14n_document(&out, tree, node, opts);
    } else {
        c14n_subtree(&out, tree, node, opts);
    }
    return sbuf_finish(&out, out_len);
}
