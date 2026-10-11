#include "serialize/internal.h"
#include "serialize/c14n_writers.h"

#include "dom/tree.h"
#include "dom/tree_internal.h"

#include <stdlib.h>
#include <string.h>

static const char C14N_XML[] = "http://www.w3.org/XML/1998/namespace";
#define C14N_STACK_ATTRS 32

static int c14n_prefix_forced(const th_c14n_opts *opts, const char *prefix) {
    int forced = 0;
    for (Py_ssize_t index = 0; index < opts->inclusive_count && !forced; index++) {
        forced = strcmp(opts->inclusive[index], prefix) == 0;
    }
    return forced;
}

static int c14n_name_is_nsdecl(const char *name, Py_ssize_t len) {
    return (len == 5 && memcmp(name, "xmlns", 5) == 0) || (len > 6 && memcmp(name, "xmlns:", 6) == 0);
}

typedef struct {
    const char *prefix;
    Py_ssize_t prefix_len;
    const Py_UCS4 *uri;
    Py_ssize_t uri_len;
} c14n_xml_ns;

typedef struct {
    const char *name;
    Py_ssize_t name_len;
    const char *local;
    Py_ssize_t local_len;
    const Py_UCS4 *uri;
    const char *ascii_uri;
    Py_ssize_t uri_len;
    const Py_UCS4 *value;
    Py_ssize_t value_len;
} c14n_xml_attr;

static int c14n_xml_ns_index(const c14n_xml_ns *namespaces, Py_ssize_t count, const char *prefix,
                             Py_ssize_t prefix_len) {
    for (Py_ssize_t index = 0; index < count; index++) {
        if (namespaces[index].prefix_len == prefix_len &&
            memcmp(namespaces[index].prefix, prefix, (size_t)prefix_len) == 0) {
            return (int)index;
        }
    }
    return -1;
}

static Py_ssize_t c14n_xml_ns_collect(th_tree *tree, const th_node *node, c14n_xml_ns *namespaces) {
    Py_ssize_t count = 0;
    for (const th_node *ancestor = node; ancestor != NULL; ancestor = ancestor->parent) {
        if (ancestor->type != TH_NODE_ELEMENT) {
            continue;
        }
        for (Py_ssize_t index = 0; index < ancestor->attr_count; index++) {
            Py_ssize_t name_len;
            const char *name = th_attr_name(tree, ancestor->attrs[index].name_atom, &name_len);
            if (!c14n_name_is_nsdecl(name, name_len)) {
                continue;
            }
            const char *prefix = name_len == 5 ? "" : name + 6;
            Py_ssize_t prefix_len = name_len == 5 ? 0 : name_len - 6;
            if (prefix_len == 3 && memcmp(prefix, "xml", 3) == 0) {
                continue;
            }
            if (c14n_xml_ns_index(namespaces, count, prefix, prefix_len) < 0) {
                namespaces[count++] =
                    (c14n_xml_ns){prefix, prefix_len, ancestor->attrs[index].value, ancestor->attrs[index].value_len};
            }
        }
    }
    return count;
}

static int c14n_xml_uri_equal(const c14n_xml_ns *left, const c14n_xml_ns *right) {
    return left->uri_len == right->uri_len &&
           memcmp(left->uri, right->uri, (size_t)left->uri_len * sizeof(Py_UCS4)) == 0;
}

static int c14n_xml_prefix_eq_tag(const c14n_xml_ns *namespace, const th_node *node) {
    Py_ssize_t index = 0;
    Py_ssize_t byte = 0;
    while (index < node->text_len && node->text[index] != ':') {
        Py_UCS4 character = node->text[index++];
        if (character < 0x80) {
            if (byte >= namespace->prefix_len || (unsigned char)namespace->prefix[byte] != character) {
                return 0;
            }
            byte++;
        } else {
            unsigned char encoded[4];
            Py_ssize_t length;
            if (character < 0x800) {
                encoded[0] = (unsigned char)(0xC0 | (character >> 6));
                encoded[1] = (unsigned char)(0x80 | (character & 0x3F));
                length = 2;
            } else if (character < 0x10000) {
                encoded[0] = (unsigned char)(0xE0 | (character >> 12));
                encoded[1] = (unsigned char)(0x80 | ((character >> 6) & 0x3F));
                encoded[2] = (unsigned char)(0x80 | (character & 0x3F));
                length = 3;
            } else {
                encoded[0] = (unsigned char)(0xF0 | (character >> 18));
                encoded[1] = (unsigned char)(0x80 | ((character >> 12) & 0x3F));
                encoded[2] = (unsigned char)(0x80 | ((character >> 6) & 0x3F));
                encoded[3] = (unsigned char)(0x80 | (character & 0x3F));
                length = 4;
            }
            if (byte + length > namespace->prefix_len ||
                memcmp(namespace->prefix + byte, encoded, (size_t)length) != 0) {
                return 0;
            }
            byte += length;
        }
    }
    return index < node->text_len && byte == namespace->prefix_len;
}

static int c14n_xml_prefix_used(th_tree *tree, const th_node *node, const c14n_xml_ns *namespace) {
    if (namespace->prefix_len == 0) {
        for (Py_ssize_t index = 0; index < node->text_len; index++) {
            if (node->text[index] == ':') {
                return 0;
            }
        }
        return 1;
    }
    if (c14n_xml_prefix_eq_tag(namespace, node)) {
        return 1;
    }
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, node->attrs[index].name_atom, &name_len);
        if (name_len > namespace->prefix_len && name[namespace->prefix_len] == ':' &&
            memcmp(name, namespace->prefix, (size_t)namespace->prefix_len) == 0) {
            return 1;
        }
    }
    return 0;
}

static int c14n_xml_ns_binding(th_tree *tree, const th_node *node, const c14n_xml_ns *namespace, c14n_xml_ns *binding) {
    for (const th_node *ancestor = node; ancestor != NULL; ancestor = ancestor->parent) {
        if (ancestor->type != TH_NODE_ELEMENT) {
            continue;
        }
        for (Py_ssize_t index = 0; index < ancestor->attr_count; index++) {
            Py_ssize_t name_len;
            const char *name = th_attr_name(tree, ancestor->attrs[index].name_atom, &name_len);
            if (!c14n_name_is_nsdecl(name, name_len)) {
                continue;
            }
            const char *prefix = name_len == 5 ? "" : name + 6;
            Py_ssize_t prefix_len = name_len == 5 ? 0 : name_len - 6;
            if (prefix_len == namespace->prefix_len && memcmp(prefix, namespace->prefix, (size_t)prefix_len) == 0) {
                *binding =
                    (c14n_xml_ns){prefix, prefix_len, ancestor->attrs[index].value, ancestor->attrs[index].value_len};
                return 1;
            }
        }
    }
    return 0;
}

static int c14n_xml_ns_render(th_tree *tree, const th_node *node, const th_node *apex, const th_c14n_opts *opts,
                              const c14n_xml_ns *namespace) {
    int reset_default = 0;
    if (opts->exclusive) {
        int forced = c14n_prefix_forced(opts, namespace->prefix_len == 0 ? "#default" : namespace->prefix);
        if (!c14n_xml_prefix_used(tree, node, namespace) && !forced) {
            return 0;
        }
        for (const th_node *ancestor = node == apex ? NULL : node->parent; ancestor != NULL;
             ancestor = ancestor->parent) {
            if (ancestor->type != TH_NODE_ELEMENT) { /* GCOVR_EXCL_BR_LINE: parsed XML has only element ancestors */
                continue;                            /* GCOVR_EXCL_LINE: parsed XML has only element ancestors here */
            }
            if (c14n_xml_prefix_used(tree, ancestor, namespace) || forced) {
                c14n_xml_ns binding;
                int has_binding = c14n_xml_ns_binding(tree, ancestor, namespace, &binding);
                if ((has_binding && c14n_xml_uri_equal(namespace, &binding)) ||
                    (!has_binding && namespace->uri_len == 0)) {
                    return 0;
                }
                reset_default = has_binding && binding.uri_len > 0;
                break;
            }
            if (ancestor == apex) {
                break;
            }
        }
    } else if (node != apex) {
        c14n_xml_ns binding;
        int has_binding = c14n_xml_ns_binding(tree, node->parent, namespace, &binding);
        if ((has_binding && c14n_xml_uri_equal(namespace, &binding)) || (!has_binding && namespace->uri_len == 0)) {
            return 0;
        }
        reset_default = has_binding && binding.uri_len > 0;
    }
    return namespace->prefix_len > 0 || namespace->uri_len > 0 || reset_default;
}

static int c14n_xml_ns_cmp(const void *left, const void *right) {
    const c14n_xml_ns *first = left;
    const c14n_xml_ns *second = right;
    Py_ssize_t common = first->prefix_len < second->prefix_len ? first->prefix_len : second->prefix_len;
    int order = memcmp(first->prefix, second->prefix, (size_t)common);
    return order != 0 ? order : (first->prefix_len > second->prefix_len) - (first->prefix_len < second->prefix_len);
}

static c14n_xml_attr c14n_xml_make_attr(const char *name, Py_ssize_t name_len, const Py_UCS4 *value,
                                        Py_ssize_t value_len, const c14n_xml_ns *namespaces, Py_ssize_t ns_count) {
    c14n_xml_attr attr = {name, name_len, name, name_len, NULL, "", 0, value, value_len};
    const char *colon = memchr(name, ':', (size_t)name_len);
    if (colon != NULL) {
        Py_ssize_t prefix_len = colon - name;
        attr.local = colon + 1;
        attr.local_len = name_len - prefix_len - 1;
        if (prefix_len == 3 && memcmp(name, "xml", 3) == 0) {
            attr.ascii_uri = C14N_XML;
            attr.uri_len = (Py_ssize_t)sizeof(C14N_XML) - 1;
        } else {
            int index = c14n_xml_ns_index(namespaces, ns_count, name, prefix_len);
            if (index >= 0) { /* GCOVR_EXCL_BR_LINE: XML parser rejects undeclared attribute prefixes */
                attr.uri = namespaces[index].uri;
                attr.uri_len = namespaces[index].uri_len;
            }
        }
    }
    return attr;
}

static Py_UCS4 c14n_xml_uri_char(const c14n_xml_attr *attr, Py_ssize_t index) {
    return attr->uri != NULL ? attr->uri[index] : (unsigned char)attr->ascii_uri[index];
}

static int c14n_xml_attr_cmp(const void *left, const void *right) {
    const c14n_xml_attr *first = left;
    const c14n_xml_attr *second = right;
    Py_ssize_t common = first->uri_len < second->uri_len ? first->uri_len : second->uri_len;
    for (Py_ssize_t index = 0; index < common; index++) {
        Py_UCS4 left_char = c14n_xml_uri_char(first, index);
        Py_UCS4 right_char = c14n_xml_uri_char(second, index);
        if (left_char != right_char) {
            return left_char < right_char ? -1 : 1;
        }
    }
    if (first->uri_len != second->uri_len) {
        return first->uri_len < second->uri_len ? -1 : 1;
    }
    common = first->local_len < second->local_len ? first->local_len : second->local_len;
    int order = memcmp(first->local, second->local, (size_t)common);
    return order != 0 ? order : (first->local_len > second->local_len) - (first->local_len < second->local_len);
}

static int c14n_xml_already_has(const c14n_xml_attr *attrs, Py_ssize_t count, const char *name, Py_ssize_t name_len) {
    for (Py_ssize_t index = 0; index < count; index++) {
        if (attrs[index].name_len == name_len && memcmp(attrs[index].name, name, (size_t)name_len) == 0) {
            return 1;
        }
    }
    return 0;
}

static void c14n_xml_open_tag(sbuf *out, th_tree *tree, th_node *node, const th_node *apex, const th_c14n_opts *opts) {
    sbuf_putc(out, '<');
    sbuf_put_ucs4(out, node->text, node->text_len);

    Py_ssize_t namespace_capacity = 0;
    for (const th_node *ancestor = node; ancestor != NULL; ancestor = ancestor->parent) {
        if (ancestor->type == TH_NODE_ELEMENT) {
            namespace_capacity += ancestor->attr_count;
        }
    }
    c14n_xml_ns stack_namespaces[C14N_STACK_ATTRS];
    c14n_xml_ns *namespaces = namespace_capacity <= C14N_STACK_ATTRS
                                  ? stack_namespaces
                                  : PyMem_Malloc((size_t)namespace_capacity * sizeof(c14n_xml_ns));
    if (namespaces == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        out->failed = 1;      /* GCOVR_EXCL_LINE: allocation-failure path */
        return;               /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t ns_count = c14n_xml_ns_collect(tree, node, namespaces);
    if (ns_count > 1) {
        qsort(namespaces, (size_t)ns_count, sizeof(c14n_xml_ns), c14n_xml_ns_cmp);
    }
    for (Py_ssize_t index = 0; index < ns_count; index++) {
        if (!c14n_xml_ns_render(tree, node, apex, opts, &namespaces[index])) {
            continue;
        }
        sbuf_puts(out, " xmlns");
        if (namespaces[index].prefix_len > 0) {
            sbuf_putc(out, ':');
            sbuf_put_utf8(out, namespaces[index].prefix, namespaces[index].prefix_len);
        }
        sbuf_puts(out, "=\"");
        c14n_put_attr(out, namespaces[index].uri, namespaces[index].uri_len);
        sbuf_putc(out, '"');
    }

    Py_ssize_t attr_capacity = node->attr_count;
    if (node == apex) {
        for (const th_node *ancestor = node->parent; ancestor != NULL; ancestor = ancestor->parent) {
            if (ancestor->type == TH_NODE_ELEMENT) {
                attr_capacity += ancestor->attr_count;
            }
        }
    }
    c14n_xml_attr stack_attrs[C14N_STACK_ATTRS];
    c14n_xml_attr *attrs =
        attr_capacity <= C14N_STACK_ATTRS ? stack_attrs : PyMem_Malloc((size_t)attr_capacity * sizeof(c14n_xml_attr));
    if (attrs == NULL) {                      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        if (namespaces != stack_namespaces) { /* GCOVR_EXCL_LINE: allocation failure cannot be forced */
            PyMem_Free(namespaces);           /* GCOVR_EXCL_LINE: allocation-failure path */
        } /* GCOVR_EXCL_LINE: allocation-failure path */
        out->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t count = 0;
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, node->attrs[index].name_atom, &name_len);
        if (!c14n_name_is_nsdecl(name, name_len)) {
            attrs[count++] = c14n_xml_make_attr(name, name_len, node->attrs[index].value, node->attrs[index].value_len,
                                                namespaces, ns_count);
        }
    }
    if (node == apex) {
        for (const th_node *ancestor = node->parent; ancestor != NULL; ancestor = ancestor->parent) {
            if (ancestor->type != TH_NODE_ELEMENT) {
                continue;
            }
            for (Py_ssize_t index = 0; index < ancestor->attr_count; index++) {
                Py_ssize_t name_len;
                const char *name = th_attr_name(tree, ancestor->attrs[index].name_atom, &name_len);
                if (name_len <= 4 || memcmp(name, "xml:", 4) != 0 ||
                    (opts->version == 1 && name_len == 6 && memcmp(name, "xml:id", 6) == 0)) {
                    continue;
                }
                if (!c14n_xml_already_has(attrs, count, name, name_len)) {
                    attrs[count++] = c14n_xml_make_attr(name, name_len, ancestor->attrs[index].value,
                                                        ancestor->attrs[index].value_len, namespaces, ns_count);
                }
            }
        }
    }
    if (count > 1) {
        qsort(attrs, (size_t)count, sizeof(c14n_xml_attr), c14n_xml_attr_cmp);
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
    if (namespaces != stack_namespaces) {
        PyMem_Free(namespaces);
    }
    sbuf_putc(out, '>');
}

#if defined(_MSC_VER)
#define C14N_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define C14N_NOINLINE __attribute__((noinline))
#else
#define C14N_NOINLINE
#endif

static C14N_NOINLINE th_node *c14n_xml_step(sbuf *out, th_tree *tree, th_node *node, const th_node *apex,
                                            const th_c14n_opts *opts) {
    th_node *descend = NULL;
    switch ((enum th_node_type)node->type) { /* GCOVR_EXCL_BR_LINE: node types are exhaustive */
    case TH_NODE_ELEMENT:
        c14n_xml_open_tag(out, tree, node, apex, opts);
        if (node->first_child != NULL) {
            descend = node->first_child;
        } else {
            c14n_close_tag(out, node);
        }
        break;
    case TH_NODE_TEXT:
    case TH_NODE_CDATA:
        c14n_put_text(out, need_text(tree, node), node->text_len);
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
    case TH_NODE_DOCTYPE:
        break;
    case TH_NODE_CONTENT:            /* GCOVR_EXCL_LINE: parsed XML does not create content nodes */
    case TH_NODE_DOCUMENT:           /* GCOVR_EXCL_LINE: documents use c14n_xml_document */
        descend = node->first_child; /* GCOVR_EXCL_LINE: unreachable from parsed XML subtree */
        break;                       /* GCOVR_EXCL_LINE: unreachable from parsed XML subtree */
    }
    if (descend != NULL) {
        return descend;
    }
    while (node != apex) {
        if (node->next_sibling != NULL) {
            return node->next_sibling;
        }
        node = node->parent;
        if (node->type == TH_NODE_ELEMENT) { /* GCOVR_EXCL_BR_LINE: parsed XML subtree parents are elements */
            c14n_close_tag(out, node);
        }
    }
    return NULL;
}

static void c14n_xml_subtree(sbuf *out, th_tree *tree, th_node *apex, const th_c14n_opts *opts) {
    th_node *node = apex;
    while (node != NULL) {
        node = c14n_xml_step(out, tree, node, apex, opts);
    }
}

static void c14n_xml_document(sbuf *out, th_tree *tree, th_node *doc, const th_c14n_opts *opts) {
    int seen_root = 0;
    for (th_node *child = doc->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT) {
            c14n_xml_subtree(out, tree, child, opts);
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

Py_UCS4 *th_node_canonicalize_xml(th_tree *tree, th_node *node, const th_c14n_opts *opts, Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0};
    if (node->type == TH_NODE_DOCUMENT) {
        c14n_xml_document(&out, tree, node, opts);
    } else {
        c14n_xml_subtree(&out, tree, node, opts);
    }
    return sbuf_finish(&out, out_len);
}
