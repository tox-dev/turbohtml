/* RSS 2.0 / Atom 1.0 / RDF(RSS 1.0) feed extraction over a parsed tree, the C engine behind Document.feed() (wired into
   the document method table in dom/document.c) and the turbohtml.extract.feed(xml) facade.

   feedparser is the canonical Python feed library and htmlparser2's parseFeed the compact minimal model; both normalize
   an RSS <item>, an Atom <entry>, and an RDF item into one shape. This follows htmlparser2's minimal-first design (a
   dozen fields, first-present-wins precedence) with feedparser's field precedence for the cases that matter (the
   content:encoded/content/summary/description chain, guid-as-permalink link, the Atom rel="alternate" link selection).

   The facade parses a feed with parse_xml and falls back to the HTML tree builder when it is not well-formed, so the
   walk reads either tree. Tag names compare ASCII case-insensitively, which covers the XML tree's lastBuildDate and the
   HTML tree's lowercased lastbuilddate alike. A field's value is its Text and CDATA character data, except an Atom
   XHTML construct, which keeps the markup inside its XHTML div. HTML also makes <link> void, so an RSS/RDF
   <link>URL</link> leaves the URL as the void element's next
   text sibling, where the XML tree keeps it as the element's own text.

   The walk runs under the per-tree critical section so a concurrent mutation cannot relink the tree mid-walk, and hands
   the gathered plain str/None fields to the Feed and Entry NamedTuple classes the thin Python facade defines and
   registers on import (turbohtml._feed), so the typed result classes live in Python while every walk stays in C.
 */

#include "core/ascii.h"
#include "core/common.h"

#include "tokenizer/binding.h" /* Py_BEGIN_CRITICAL_SECTION shim for the GIL/pre-3.13 build */
#include "dom/nodes.h"
#include "serialize/xml_text.h"

#include <string.h>

static const char DC_URI[] = "http://purl.org/dc/elements/1.1/";
static const char CONTENT_URI[] = "http://purl.org/rss/1.0/modules/content/";
static const char RDF_URI[] = "http://www.w3.org/1999/02/22-rdf-syntax-ns#";
static const char RSS_URI[] = "http://purl.org/rss/1.0/";
static const char ATOM_URI[] = "http://www.w3.org/2005/Atom";
static const char XHTML_URI[] = "http://www.w3.org/1999/xhtml";

/* One candidate element tag for a normalized field: the name and its length, tried in precedence order. */
typedef struct {
    const char *name;
    Py_ssize_t len;
} feed_tag;

/* Whether the UCS4 run equals the ASCII literal [lit, lit + lit_len) ignoring ASCII case. The parser lowercases tag and
   attribute names, so an exact compare would do; folding keeps a hand-written test feed's mixed case (isPermaLink)
   matching too. */
static int ucs4_ieq(const Py_UCS4 *value, Py_ssize_t len, const char *lit, Py_ssize_t lit_len) {
    if (len != lit_len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (lower_ascii(value[index]) != (Py_UCS4)(unsigned char)lit[index]) {
            return 0;
        }
    }
    return 1;
}

/* Whether element `node`'s tag name equals the ASCII literal [name, name + len). */
static int tag_is(const th_node *node, const char *name, Py_ssize_t len) {
    return ucs4_ieq(node->text, node->text_len, name, len);
}

static int ucs4_eq_ascii(const Py_UCS4 *value, Py_ssize_t len, const char *lit) {
    if (value == NULL || len != (Py_ssize_t)strlen(lit)) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (value[index] != (Py_UCS4)(unsigned char)lit[index]) {
            return 0;
        }
    }
    return 1;
}

static int namespace_is(th_tree *tree, th_node *node, Py_ssize_t prefix_len, const char *uri) {
    for (th_node *ancestor = node; ancestor != NULL; ancestor = ancestor->parent) {
        if (ancestor->type != TH_NODE_ELEMENT) {
            continue;
        }
        for (Py_ssize_t index = 0; index < ancestor->attr_count; index++) {
            th_node_attr *attr = &ancestor->attrs[index];
            Py_ssize_t name_len;
            const char *name = th_attr_name(tree, attr->name_atom, &name_len);
            if (prefix_len == 0) {
                if (name_len == 5 && memcmp(name, "xmlns", 5) == 0) {
                    return ucs4_eq_ascii(attr->value, attr->value_len, uri);
                }
                continue;
            }
            if (name_len < 7 || memcmp(name, "xmlns:", 6) != 0) {
                continue;
            }
            int ascii = 1;
            for (Py_ssize_t offset = 6; offset < name_len; offset++) {
                if ((unsigned char)name[offset] >= 0x80) {
                    ascii = 0;
                    break;
                }
            }
            if (ascii) {
                if (name_len - 6 == prefix_len) {
                    int same = 1;
                    for (Py_ssize_t offset = 0; same && offset < prefix_len; offset++) {
                        same = (unsigned char)name[offset + 6] == node->text[offset];
                    }
                    if (same) {
                        return ucs4_eq_ascii(attr->value, attr->value_len, uri);
                    }
                }
                continue;
            }
            PyObject *prefix = PyUnicode_DecodeUTF8(name + 6, name_len - 6, "strict");
            if (prefix == NULL) { /* GCOVR_EXCL_BR_LINE: interned attribute names are valid UTF-8; allocation only */
                return -1;        /* GCOVR_EXCL_LINE: allocation failure */
            }
            int same = PyUnicode_GET_LENGTH(prefix) == prefix_len;
            for (Py_ssize_t offset = 0; same && offset < prefix_len; offset++) {
                same = PyUnicode_READ_CHAR(prefix, offset) == node->text[offset];
            }
            Py_DECREF(prefix);
            if (same) {
                return ucs4_eq_ascii(attr->value, attr->value_len, uri);
            }
        }
    }
    return 0;
}

static int namespace_bytes_is(th_tree *tree, th_node *node, const char *prefix, Py_ssize_t prefix_len,
                              const char *uri) {
    for (th_node *ancestor = node; ancestor != NULL; ancestor = ancestor->parent) {
        if (ancestor->type != TH_NODE_ELEMENT) {
            continue;
        }
        for (Py_ssize_t index = 0; index < ancestor->attr_count; index++) {
            th_node_attr *attr = &ancestor->attrs[index];
            Py_ssize_t name_len;
            const char *name = th_attr_name(tree, attr->name_atom, &name_len);
            if (name_len == prefix_len + 6 && memcmp(name, "xmlns:", 6) == 0 &&
                memcmp(name + 6, prefix, (size_t)prefix_len) == 0) {
                return ucs4_eq_ascii(attr->value, attr->value_len, uri);
            }
        }
    }
    return 0;
}

static int qualified_tag_is(th_tree *tree, th_node *node, const char *local, const char *uri) {
    Py_ssize_t local_len = (Py_ssize_t)strlen(local);
    if (node->text_len == local_len && ucs4_eq_ascii(node->text, local_len, local)) {
        return namespace_is(tree, node, 0, uri);
    }
    Py_ssize_t colon = node->text_len - local_len - 1;
    return colon > 0 && node->text[colon] == ':' && ucs4_eq_ascii(node->text + colon + 1, local_len, local)
               ? namespace_is(tree, node, colon, uri)
               : 0;
}

static int feed_tag_is(th_tree *tree, th_node *node, const char *name, Py_ssize_t len, const char *uri) {
    if (tag_is(node, name, len)) {
        return 1;
    }
    if (uri == NULL || node->text_len <= len + 1) {
        return 0;
    }
    Py_ssize_t colon = node->text_len - len - 1;
    return node->text[colon] == ':' && th_tree_is_xml(tree) && ucs4_eq_ascii(node->text + colon + 1, len, name)
               ? namespace_is(tree, node, colon, uri)
               : 0;
}

/* The first direct element child of `parent` whose tag equals [name, name + len), or NULL when it has none. */
static th_node *child_named(th_node *parent, const char *name, Py_ssize_t len) {
    for (th_node *child = parent->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT && tag_is(child, name, len)) {
            return child;
        }
    }
    return NULL;
}

static th_node *feed_child_named(th_tree *tree, th_node *parent, const char *name, Py_ssize_t len, const char *uri,
                                 int *error) {
    if (uri == NULL) {
        return child_named(parent, name, len);
    }
    for (th_node *child = parent->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        int match = feed_tag_is(tree, child, name, len, uri);
        if (match < 0) { /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
            *error = 1;  /* GCOVR_EXCL_LINE: allocation failure */
            return NULL; /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (match) {
            return child;
        }
    }
    return NULL;
}

/* A UCS4 run as a new str with leading and trailing ASCII whitespace stripped. NULL only on the excluded
   allocation-failure path. */
static PyObject *ucs4_trimmed(const Py_UCS4 *value, Py_ssize_t len) {
    Py_ssize_t start = 0;
    Py_ssize_t end = len;
    while (start < end && is_space(value[start])) {
        start++;
    }
    while (end > start && is_space(value[end - 1])) {
        end--;
    }
    return ucs4_to_str(value + start, end - start);
}

/* The whitespace-trimmed character data of an element as a new str (empty when it holds only whitespace): its Text and
   CDATA descendants in document order. An XML feed wraps markup-bearing fields in CDATA
   (<description><![CDATA[<p>...</p>]]></description>), and the field's value is that markup verbatim, as an escaped
   description's is. NULL only on the excluded allocation-failure path. */
static PyObject *node_text_trimmed(th_tree *tree, th_node *node) {
    sbuf buffer = {NULL, 0, 0, 0};
    for (th_node *child = node->first_child; child != NULL; child = preorder_next(child, node)) {
        if (child->type != TH_NODE_TEXT && child->type != TH_NODE_CDATA) {
            continue;
        }
        Py_ssize_t len;
        Py_UCS4 *data = th_node_data(tree, child, &len);
        if (data == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            buffer.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            break;             /* GCOVR_EXCL_LINE */
        }
        sbuf_put_ucs4(&buffer, data, len);
        PyMem_Free(data);
    }
    if (buffer.failed) {         /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(buffer.data); /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = ucs4_trimmed(buffer.data, buffer.len);
    PyMem_Free(buffer.data);
    return result;
}

static int xhtml_element(th_tree *tree, th_node *node, th_node *element) {
    *element = *node;
    for (Py_ssize_t index = 0; index < node->text_len; index++) {
        if (node->text[index] != ':') {
            continue;
        }
        int xhtml = namespace_is(tree, node, index, XHTML_URI);
        if (xhtml < 0) { /* GCOVR_EXCL_BR_LINE: namespace lookup fails only on allocation */
            return -1;   /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (xhtml) {
            element->text += index + 1;
            element->text_len -= index + 1;
        }
        return xhtml;
    }
    return namespace_is(tree, node, 0, XHTML_URI);
}

static int xhtml_inherit_prefix(sbuf *out, th_tree *tree, th_node *node, const char *prefix, Py_ssize_t prefix_len) {
    if (prefix_len == 3 && memcmp(prefix, "xml", 3) == 0) {
        return 0;
    }
    for (th_node *ancestor = node; ancestor != NULL; ancestor = ancestor->parent) {
        for (Py_ssize_t index = 0; index < ancestor->attr_count; index++) {
            th_node_attr *attr = &ancestor->attrs[index];
            Py_ssize_t len;
            const char *name = th_attr_name(tree, attr->name_atom, &len);
            if (len != prefix_len + 6 || memcmp(name, "xmlns:", 6) != 0 ||
                memcmp(name + 6, prefix, (size_t)prefix_len) != 0) {
                continue;
            }
            if (attr->value == NULL || attr->value_len == 0) {
                return 1;
            }
            if (ancestor != node) {
                sbuf_puts(out, " xmlns:");
                sbuf_put_utf8(out, prefix, prefix_len);
                sbuf_puts(out, "=\"");
                sbuf_put_xml_text(out, attr->value, attr->value_len, 1);
                sbuf_putc(out, '"');
            }
            return 0;
        }
    }
    return 1;
}

static int xhtml_open_tag(sbuf *out, th_tree *tree, th_node *node, th_node *element) {
    sbuf_putc(out, '<');
    sbuf_put_ucs4(out, element->text, element->text_len);
    Py_ssize_t tag_prefix_len = 0;
    const char *tag_prefix = NULL;
    PyObject *tag_prefix_owner = NULL;
    if (element->text == node->text) {
        for (Py_ssize_t index = 0; index < element->text_len; index++) {
            if (element->text[index] == ':') {
                tag_prefix_owner = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, element->text, index);
                if (tag_prefix_owner == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    return -1;                  /* GCOVR_EXCL_LINE: allocation failure */
                }
                tag_prefix = PyUnicode_AsUTF8AndSize(tag_prefix_owner, &tag_prefix_len);
                if (tag_prefix == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure */
                    Py_DECREF(tag_prefix_owner); /* GCOVR_EXCL_LINE: allocation failure */
                    return -1;                   /* GCOVR_EXCL_LINE: allocation failure */
                }
                if (xhtml_inherit_prefix(out, tree, node, tag_prefix, tag_prefix_len)) {
                    Py_DECREF(tag_prefix_owner);
                    return 1;
                }
                break;
            }
        }
    }
    for (Py_ssize_t index = 0; index < element->attr_count; index++) {
        th_node_attr *attr = &element->attrs[index];
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, attr->name_atom, &name_len);
        const char *colon = memchr(name, ':', (size_t)name_len);
        if (colon != NULL && !(name_len >= 6 && memcmp(name, "xmlns:", 6) == 0)) {
            Py_ssize_t prefix_len = colon - name;
            int already_bound = tag_prefix_len == prefix_len && memcmp(tag_prefix, name, (size_t)prefix_len) == 0;
            for (Py_ssize_t previous = 0; !already_bound && previous < index; previous++) {
                Py_ssize_t previous_len;
                const char *previous_name = th_attr_name(tree, element->attrs[previous].name_atom, &previous_len);
                already_bound = previous_len > prefix_len && previous_name[prefix_len] == ':' &&
                                memcmp(previous_name, name, (size_t)prefix_len) == 0;
            }
            if (!already_bound) {
                if (xhtml_inherit_prefix(out, tree, node, name, prefix_len)) {
                    Py_XDECREF(tag_prefix_owner);
                    return 1;
                }
            }
        }
        sbuf_putc(out, ' ');
        sbuf_put_utf8(out, name, name_len);
        sbuf_puts(out, "=\"");
        sbuf_put_xml_text(out, attr->value, attr->value_len, 1);
        sbuf_putc(out, '"');
    }
    Py_XDECREF(tag_prefix_owner);
    return 0;
}

static void xhtml_close_tag(sbuf *out, th_node *element) {
    sbuf_puts(out, "</");
    sbuf_put_ucs4(out, element->text, element->text_len);
    sbuf_putc(out, '>');
}

static int xhtml_void_tag(th_node *node) {
    static const feed_tag VOID[] = {{"area", 4},   {"base", 4},  {"basefont", 8}, {"bgsound", 7}, {"br", 2},
                                    {"col", 3},    {"embed", 5}, {"hr", 2},       {"img", 3},     {"input", 5},
                                    {"keygen", 6}, {"link", 4},  {"meta", 4},     {"param", 5},   {"source", 6},
                                    {"track", 5},  {"wbr", 3}};
    for (size_t index = 0; index < sizeof(VOID) / sizeof(VOID[0]); index++) {
        if (tag_is(node, VOID[index].name, VOID[index].len)) {
            return 1;
        }
    }
    return 0;
}

static PyObject *atom_xhtml_text(th_tree *tree, th_node *node) {
    if (!th_tree_is_xml(tree) || node->attr_count == 0) {
        return node_text_trimmed(tree, node);
    }
    Py_ssize_t type = th_node_attr_find(tree, node, "type", 4);
    if (type < 0 || !ucs4_eq_ascii(node->attrs[type].value, node->attrs[type].value_len, "xhtml")) {
        return node_text_trimmed(tree, node);
    }
    th_node *div = NULL;
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT) {
            div = child;
            break;
        }
    }
    if (div == NULL) {
        return node_text_trimmed(tree, node);
    }
    int valid = qualified_tag_is(tree, div, "div", XHTML_URI);
    if (valid < 0) { /* GCOVR_EXCL_BR_LINE: namespace lookup fails only on allocation */
        return NULL; /* GCOVR_EXCL_LINE: allocation failure */
    }
    if (!valid) {
        return node_text_trimmed(tree, node);
    }
    sbuf out = {NULL, 0, 0, 0};
    th_serialize_opts opts = {0};
    opts.xml = 1;
    for (th_node *child = div->first_child; child != NULL;) {
        if (child->type == TH_NODE_ELEMENT) {
            th_node element;
            int xhtml = xhtml_element(tree, child, &element);
            if (xhtml < 0) {          /* GCOVR_EXCL_BR_LINE: allocation failure */
                PyMem_Free(out.data); /* GCOVR_EXCL_LINE: allocation failure */
                return NULL;          /* GCOVR_EXCL_LINE: allocation failure */
            }
            int opened = xhtml_open_tag(&out, tree, child, &element);
            if (opened != 0) {
                PyMem_Free(out.data);
                return opened < 0 ? NULL : node_text_trimmed(tree, node); /* GCOVR_EXCL_BR_LINE: allocation failure */
            }
            if (child->first_child != NULL) {
                sbuf_putc(&out, '>');
                child = child->first_child;
                continue;
            }
            if (xhtml && !xhtml_void_tag(&element)) {
                sbuf_putc(&out, '>');
                xhtml_close_tag(&out, &element);
            } else {
                sbuf_puts(&out, "/>");
            }
        }
        if (child->type == TH_NODE_TEXT || child->type == TH_NODE_CDATA) {
            Py_ssize_t len;
            Py_UCS4 *data = th_node_data(tree, child, &len);
            if (data == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure */
                PyMem_Free(out.data);    /* GCOVR_EXCL_LINE: allocation failure */
                return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation failure */
            }
            sbuf_put_xml_text(&out, data, len, 0);
            PyMem_Free(data);
        } else if (child->type == TH_NODE_COMMENT || child->type == TH_NODE_PI) {
            Py_ssize_t len;
            Py_UCS4 *data = th_node_serialize(tree, child, &opts, NULL, 0, &len);
            if (data == NULL) {          /* GCOVR_EXCL_BR_LINE: serialization fails only on allocation */
                PyMem_Free(out.data);    /* GCOVR_EXCL_LINE: allocation failure */
                return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation failure */
            }
            sbuf_put_ucs4(&out, data, len);
            PyMem_Free(data);
        }
        while (child->next_sibling == NULL && child->parent != div) {
            child = child->parent;
            th_node element;
            if (xhtml_element(tree, child, &element) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                PyMem_Free(out.data);                       /* GCOVR_EXCL_LINE: allocation failure */
                return NULL;                                /* GCOVR_EXCL_LINE: allocation failure */
            }
            xhtml_close_tag(&out, &element);
        }
        child = child->next_sibling;
    }
    if (out.failed) {            /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyMem_Free(out.data);    /* GCOVR_EXCL_LINE: allocation failure */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *result = ucs4_trimmed(out.data, out.len);
    PyMem_Free(out.data);
    return result;
}

/* The first non-empty trimmed text among `parent`'s direct children named by the precedence-ordered `tags`, or None
   when none is present with a non-empty value (or when `parent` is NULL, a feed whose channel the parser dropped). NULL
   only on the excluded allocation-failure path. */
static PyObject *field_text(th_tree *tree, th_node *parent, const feed_tag *tags, size_t count, const char *uri,
                            int xhtml) {
    if (parent == NULL) {
        return Py_NewRef(Py_None);
    }
    for (size_t index = 0; index < count; index++) {
        th_node *child = NULL;
        if (th_tree_is_xml(tree) && tags[index].len == 7 && memcmp(tags[index].name, "dc:date", 7) == 0) {
            for (th_node *candidate = parent->first_child; candidate != NULL; candidate = candidate->next_sibling) {
                if (candidate->type != TH_NODE_ELEMENT) {
                    continue;
                }
                int match = qualified_tag_is(tree, candidate, "date", DC_URI);
                if (match < 0) { /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
                    return NULL; /* GCOVR_EXCL_LINE: allocation failure */
                }
                if (match) {
                    child = candidate;
                    break;
                }
            }
        } else {
            int error = 0;
            child = feed_child_named(tree, parent, tags[index].name, tags[index].len, uri, &error);
            if (error) {     /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
                return NULL; /* GCOVR_EXCL_LINE: allocation failure */
            }
        }
        if (child == NULL) {
            continue;
        }
        PyObject *value = xhtml ? atom_xhtml_text(tree, child) : node_text_trimmed(tree, child);
        if (value == NULL) { /* GCOVR_EXCL_BR_LINE: node_text_trimmed fails only on allocation */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (PyUnicode_GET_LENGTH(value) > 0) {
            return value;
        }
        Py_DECREF(value);
    }
    return Py_NewRef(Py_None);
}

/* A <link>'s trimmed href attribute (the Atom form), or NULL when it carries no non-empty href (the RSS/RDF form, whose
   URL is the element's text instead). */
static PyObject *link_href(th_tree *tree, th_node *link) {
    Py_ssize_t index = th_node_attr_find(tree, link, "href", 4);
    if (index < 0 || link->attrs[index].value == NULL) {
        return NULL;
    }
    PyObject *href = ucs4_trimmed(link->attrs[index].value, link->attrs[index].value_len);
    if (href == NULL) { /* GCOVR_EXCL_BR_LINE: ucs4_trimmed fails only on allocation */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (PyUnicode_GET_LENGTH(href) == 0) {
        Py_DECREF(href);
        return NULL;
    }
    return href;
}

/* Whether an Atom <link>'s rel makes it the entry's primary link: an absent, valueless, or "alternate" rel. A rel of
   self/enclosure/edit/replies is a secondary link the walk skips unless no alternate is found. */
static int link_is_alternate(th_tree *tree, th_node *link) {
    Py_ssize_t index = th_node_attr_find(tree, link, "rel", 3);
    if (index < 0 || link->attrs[index].value == NULL) {
        return 1;
    }
    return ucs4_ieq(link->attrs[index].value, link->attrs[index].value_len, "alternate", 9);
}

/* The trimmed URL an RSS/RDF <link> without an href holds: its own text in an XML tree, the next text sibling in an
   HTML tree (where <link> is void, so the parser moves the URL out of it), empty when there is none. NULL only on the
   excluded allocation-failure path. */
static PyObject *link_text(th_tree *tree, th_node *link) {
    if (th_tree_is_xml(tree)) {
        return node_text_trimmed(tree, link);
    }
    th_node *sibling = link->next_sibling;
    if (sibling == NULL || sibling->type != TH_NODE_TEXT) {
        return PyUnicode_New(0, 0);
    }
    Py_ssize_t len;
    Py_UCS4 *data = th_node_data(tree, sibling, &len);
    if (data == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *text = ucs4_trimmed(data, len);
    PyMem_Free(data);
    return text;
}

static PyObject *resolve_link_xml_base(th_tree *tree, th_node *link, PyObject *url) {
    if (!th_tree_is_xml(tree)) {
        return url;
    }
    int kind = PyUnicode_KIND(url);
    const void *data = PyUnicode_DATA(url);
    if (is_ascii_alpha(PyUnicode_READ(kind, data, 0))) {
        for (Py_ssize_t index = 1; index < PyUnicode_GET_LENGTH(url); index++) {
            Py_UCS4 point = PyUnicode_READ(kind, data, index);
            if (point == ':') {
                return url;
            }
            if (!is_ascii_alpha(point) && !is_ascii_digit(point) && point != '+' && point != '-' && point != '.') {
                break;
            }
        }
    }
    PyObject *base_uri = NULL;
    for (th_node *node = link; node != NULL; node = node->parent) {
        if (node->type != TH_NODE_ELEMENT) {
            continue;
        }
        Py_ssize_t index = th_node_attr_find(tree, node, "xml:base", 8);
        if (index < 0) {
            continue;
        }
        th_node_attr *attr = &node->attrs[index];
        PyObject *joined = base_uri == NULL
                               ? PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, attr->value, attr->value_len)
                               : th_url_join_c14n_raw(attr->value, attr->value_len, base_uri);
        Py_XDECREF(base_uri);
        if (joined == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            Py_DECREF(url);   /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        base_uri = joined;
    }
    if (base_uri == NULL) {
        return url;
    }
    PyObject *result = th_url_join(base_uri, url);
    Py_DECREF(base_uri);
    Py_DECREF(url);
    return result;
}

/* The permalink URL of `parent`: the first Atom <link rel="alternate"> href, else the first RSS/RDF <link>'s text,
   else the first non-alternate Atom href as a fallback, else None. NULL only on the excluded allocation-failure
   path. */
static PyObject *extract_link(th_tree *tree, th_node *parent, const char *uri) {
    if (parent == NULL) {
        return Py_NewRef(Py_None);
    }
    PyObject *fallback = NULL;
    for (th_node *child = parent->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        int match = feed_tag_is(tree, child, "link", 4, uri);
        if (match < 0) {          /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
            Py_XDECREF(fallback); /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;          /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (!match) {
            continue;
        }
        PyObject *href = link_href(tree, child);
        if (href != NULL) {
            href = resolve_link_xml_base(tree, child, href);
            if (href == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure */
                Py_XDECREF(fallback); /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;          /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            if (link_is_alternate(tree, child)) {
                Py_XDECREF(fallback);
                return href;
            }
            if (fallback == NULL) {
                fallback = href;
            } else {
                Py_DECREF(href);
            }
            continue;
        }
        PyObject *text = link_text(tree, child);
        if (text == NULL) {       /* GCOVR_EXCL_BR_LINE: link_text fails only on allocation */
            Py_XDECREF(fallback); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;          /* GCOVR_EXCL_LINE */
        }
        if (PyUnicode_GET_LENGTH(text) > 0) {
            Py_XDECREF(fallback);
            return resolve_link_xml_base(tree, child, text);
        }
        Py_DECREF(text);
    }
    if (fallback != NULL) {
        return fallback;
    }
    return Py_NewRef(Py_None);
}

/* Adjacent fields follow fallback precedence. */
enum {
    FEED_TITLE,
    FEED_GUID,
    FEED_ID,
    FEED_UPDATED,
    FEED_LAST_BUILD,
    FEED_PUBLISHED,
    FEED_PUBDATE,
    FEED_DC_DATE,
    FEED_SUMMARY,
    FEED_DESCRIPTION,
    FEED_DC_DESCRIPTION,
    FEED_ENCODED,
    FEED_CONTENT,
    FEED_AUTHOR,
    FEED_CREATOR,
    FEED_FIELD_COUNT
};

static int collect_entry_fields(th_tree *tree, th_node *entry, th_node **fields, const char *uri) {
    static const feed_tag tags[FEED_FIELD_COUNT] = {{"title", 5},
                                                    {"guid", 4},
                                                    {"id", 2},
                                                    {"updated", 7},
                                                    {"lastbuilddate", 13},
                                                    {"published", 9},
                                                    {"pubdate", 7},
                                                    {"dc:date", 7},
                                                    {"summary", 7},
                                                    {"description", 11},
                                                    {"dc:description", 14},
                                                    {"content:encoded", 15},
                                                    {"content", 7},
                                                    {"author", 6},
                                                    {"dc:creator", 10}};
    static const struct {
        const char *local;
        const char *uri;
        int field;
    } modules[] = {{"date", DC_URI, FEED_DC_DATE},
                   {"description", DC_URI, FEED_DC_DESCRIPTION},
                   {"creator", DC_URI, FEED_CREATOR},
                   {"encoded", CONTENT_URI, FEED_ENCODED}};
    int xml = th_tree_is_xml(tree);
    int dc_namespace = -2;
    int content_namespace = -2;
    for (th_node *child = entry->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        int field = -1;
        switch (child->text_len) {
        case 2:
            field = FEED_ID;
            break;
        case 4:
            field = FEED_GUID;
            break;
        case 5:
            field = FEED_TITLE;
            break;
        case 6:
            field = FEED_AUTHOR;
            break;
        case 7:
            switch (lower_ascii(child->text[0])) {
            case 'u':
                field = FEED_UPDATED;
                break;
            case 'p':
                field = FEED_PUBDATE;
                break;
            case 'd':
                field = FEED_DC_DATE;
                break;
            case 's':
                field = FEED_SUMMARY;
                break;
            case 'c':
                field = FEED_CONTENT;
                break;
            default:
                break;
            }
            break;
        case 9:
            field = FEED_PUBLISHED;
            break;
        case 10:
            field = FEED_CREATOR;
            break;
        case 11:
            field = FEED_DESCRIPTION;
            break;
        case 13:
            field = FEED_LAST_BUILD;
            break;
        case 14:
            field = FEED_DC_DESCRIPTION;
            break;
        case 15:
            field = FEED_ENCODED;
            break;
        default:
            break;
        }
        if (field >= 0 && tag_is(child, tags[field].name, tags[field].len)) {
            if (xml && field == FEED_DESCRIPTION) {
                int match = qualified_tag_is(tree, child, "description", DC_URI);
                if (match < 0) { /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
                    return -1;   /* GCOVR_EXCL_LINE: allocation failure */
                }
                if (match) {
                    if (fields[FEED_DC_DESCRIPTION] == NULL) {
                        fields[FEED_DC_DESCRIPTION] = child;
                    }
                    continue;
                }
            }
            if (xml && (field == FEED_DC_DATE || field == FEED_DC_DESCRIPTION || field == FEED_CREATOR ||
                        field == FEED_ENCODED)) {
                int *cached = field == FEED_ENCODED ? &content_namespace : &dc_namespace;
                int match = child->attr_count == 0 && *cached != -2
                                ? *cached
                                : namespace_is(tree, child, field == FEED_ENCODED ? 7 : 2,
                                               field == FEED_ENCODED ? CONTENT_URI : DC_URI);
                if (match < 0) { /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
                    return -1;   /* GCOVR_EXCL_LINE: allocation failure */
                }
                if (child->attr_count == 0) {
                    *cached = match;
                }
                if (!match) {
                    continue;
                }
            }
            if (fields[field] == NULL) {
                fields[field] = child;
            }
            continue;
        }
        if (uri != NULL && xml) {
            int found = 0;
            for (Py_ssize_t colon = 1; colon < child->text_len; colon++) {
                if (child->text[colon] != ':') {
                    continue;
                }
                int match = namespace_is(tree, child, colon, uri);
                if (match < 0) { /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
                    return -1;   /* GCOVR_EXCL_LINE: allocation failure */
                }
                if (match) {
                    for (int index = 0; index < FEED_FIELD_COUNT; index++) {
                        if (memchr(tags[index].name, ':', (size_t)tags[index].len) == NULL &&
                            ucs4_ieq(child->text + colon + 1, child->text_len - colon - 1, tags[index].name,
                                     tags[index].len)) {
                            if (fields[index] == NULL) {
                                fields[index] = child;
                            }
                            found = 1;
                            break;
                        }
                    }
                }
                break;
            }
            if (found) {
                continue;
            }
        }
        if (xml) {
            for (size_t index = 0; index < sizeof(modules) / sizeof(modules[0]); index++) {
                int match = qualified_tag_is(tree, child, modules[index].local, modules[index].uri);
                if (match < 0) { /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
                    return -1;   /* GCOVR_EXCL_LINE: allocation failure */
                }
                if (match) {
                    if (fields[modules[index].field] == NULL) {
                        fields[modules[index].field] = child;
                    }
                    break;
                }
            }
        }
    }
    return 0;
}

static PyObject *entry_field_text(th_tree *tree, th_node *const *fields, size_t count, int atom) {
    for (size_t index = 0; index < count; index++) {
        if (fields[index] == NULL) {
            continue;
        }
        PyObject *value = atom ? atom_xhtml_text(tree, fields[index]) : node_text_trimmed(tree, fields[index]);
        if (value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return NULL;     /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (PyUnicode_GET_LENGTH(value) > 0) {
            return value;
        }
        Py_DECREF(value);
    }
    return Py_NewRef(Py_None);
}

/* The author display name of `parent`: an <author>'s nested <name> (the Atom form), else the <author>'s own text (an
   RSS email), else a <dc:creator>, else None. NULL only on the excluded allocation-failure path. */
static PyObject *extract_author(th_tree *tree, th_node *const *fields, const char *uri) {
    for (size_t index = 0; index < 2; index++) {
        th_node *child = fields[index];
        if (child == NULL) {
            continue;
        }
        int error = 0;
        th_node *name = feed_child_named(tree, child, "name", 4, uri, &error);
        if (error) {     /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
            return NULL; /* GCOVR_EXCL_LINE: allocation failure */
        }
        PyObject *value = node_text_trimmed(tree, name != NULL ? name : child);
        if (value == NULL) { /* GCOVR_EXCL_BR_LINE: node_text_trimmed fails only on allocation */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (PyUnicode_GET_LENGTH(value) > 0) {
            return value;
        }
        Py_DECREF(value);
    }
    return Py_NewRef(Py_None);
}

/* The entry identifier: an RSS <guid> / Atom <id> element text, else the RDF item's rdf:about attribute, else None.
   NULL only on the excluded allocation-failure path. */
static PyObject *extract_id(th_tree *tree, th_node *entry, th_node *const *fields) {
    PyObject *value = entry_field_text(tree, fields, 2, 0);
    if (value == NULL) { /* GCOVR_EXCL_BR_LINE: field_text fails only on allocation */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (value != Py_None) {
        return value;
    }
    Py_DECREF(value);
    if (!th_tree_is_xml(tree)) {
        Py_ssize_t index = th_node_attr_find(tree, entry, "rdf:about", 9);
        if (index >= 0 && entry->attrs[index].value != NULL) {
            return ucs4_trimmed(entry->attrs[index].value, entry->attrs[index].value_len);
        }
    } else {
        for (Py_ssize_t index = 0; index < entry->attr_count; index++) {
            const th_node_attr *attr = &entry->attrs[index];
            Py_ssize_t name_len;
            const char *name = th_attr_name(tree, attr->name_atom, &name_len);
            if (attr->value != NULL && name_len > 6 && name[name_len - 6] == ':' &&
                memcmp(name + name_len - 5, "about", 5) == 0 &&
                namespace_bytes_is(tree, entry, name, name_len - 6, RDF_URI)) {
                return ucs4_trimmed(attr->value, attr->value_len);
            }
        }
    }
    return Py_NewRef(Py_None);
}

/* When an entry has no link element, an RSS <guid> that is a permalink (its isPermaLink is not "false") doubles as the
   link, mirroring feedparser. Replaces *link with the guid text when one qualifies. -1 only on the excluded
   allocation-failure path. */
static int apply_guid_permalink(th_tree *tree, th_node *guid, PyObject **link) {
    if (*link != Py_None) {
        return 0;
    }
    if (guid == NULL) {
        return 0;
    }
    /* XML keeps the attribute's spelling (isPermaLink) and matches it case-sensitively; HTML lowercases it. */
    Py_ssize_t index = th_node_attr_find(tree, guid, th_tree_is_xml(tree) ? "isPermaLink" : "ispermalink", 11);
    if (index >= 0 && guid->attrs[index].value != NULL &&
        ucs4_ieq(guid->attrs[index].value, guid->attrs[index].value_len, "false", 5)) {
        return 0;
    }
    PyObject *value = node_text_trimmed(tree, guid);
    if (value == NULL) { /* GCOVR_EXCL_BR_LINE: node_text_trimmed fails only on allocation */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (PyUnicode_GET_LENGTH(value) > 0) {
        Py_SETREF(*link, value);
    } else {
        Py_DECREF(value);
    }
    return 0;
}

/* Steal `value` into slot `index` of `record`, returning -1 only on the excluded allocation-failure path (a NULL value
   a field builder could not allocate). */
static int record_set(PyObject *record, Py_ssize_t index, PyObject *value) {
    if (value == NULL) { /* GCOVR_EXCL_BR_LINE: every field builder fails only on unforceable allocation */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyTuple_SET_ITEM(record, index, value);
    return 0;
}

/* The entry's link with the guid-permalink fallback applied, ready for record_set. NULL only on the excluded
   allocation-failure path. */
static PyObject *entry_link(th_tree *tree, th_node *item, th_node *guid, const char *uri) {
    PyObject *link = extract_link(tree, item, uri);
    if (link == NULL) { /* GCOVR_EXCL_BR_LINE: extract_link fails only on allocation */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (apply_guid_permalink(tree, guid, &link) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        Py_DECREF(link);                               /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                                   /* GCOVR_EXCL_LINE */
    }
    return link;
}

/* Build one Entry(title, link, id, updated, published, summary, content, author) from an RSS <item>, an Atom <entry>,
   or an RDF item, each field the first present value in its precedence order. NULL only on the excluded
   allocation-failure path. */
static PyObject *build_entry(module_state *state, th_tree *tree, th_node *item, const char *uri) {
    th_node *fields[FEED_FIELD_COUNT] = {0};
    if (collect_entry_fields(tree, item, fields, uri) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;                                         /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *record = PyTuple_New(8);
    if (record == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int failed = 0;
    int atom = uri == ATOM_URI;
    failed |= record_set(record, 0, entry_field_text(tree, fields + FEED_TITLE, 1, atom));
    failed |= record_set(record, 1, entry_link(tree, item, fields[FEED_GUID], uri));
    failed |= record_set(record, 2, extract_id(tree, item, fields + FEED_GUID));
    failed |= record_set(record, 3, entry_field_text(tree, fields + FEED_UPDATED, 2, 0));
    failed |= record_set(record, 4, entry_field_text(tree, fields + FEED_PUBLISHED, 3, 0));
    failed |= record_set(record, 5, entry_field_text(tree, fields + FEED_SUMMARY, 3, atom));
    failed |= record_set(record, 6, entry_field_text(tree, fields + FEED_ENCODED, 2, atom));
    failed |= record_set(record, 7, extract_author(tree, fields + FEED_AUTHOR, uri));
    if (failed != 0) {     /* GCOVR_EXCL_BR_LINE: a field build fails only on unforceable allocation */
        Py_DECREF(record); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    PyObject *entry = PyObject_Call(state->entry_type, record, NULL);
    Py_DECREF(record);
    return entry;
}

/* The tuple of Entry records for every direct child of `parent` named [name, name + len) (item for RSS/RDF, entry for
   Atom), an empty tuple when `parent` is NULL. NULL only on the excluded allocation-failure path. */
static PyObject *build_entries(module_state *state, th_tree *tree, th_node *parent, const char *name, Py_ssize_t len,
                               const char *uri) {
    PyObject *list = PyList_New(0);
    if (list == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (parent != NULL) {
        for (th_node *child = parent->first_child; child != NULL; child = child->next_sibling) {
            if (child->type != TH_NODE_ELEMENT) {
                continue;
            }
            int match = feed_tag_is(tree, child, name, len, uri);
            if (match < 0) {     /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
                Py_DECREF(list); /* GCOVR_EXCL_LINE: allocation failure */
                return NULL;     /* GCOVR_EXCL_LINE: allocation failure */
            }
            if (!match) {
                continue;
            }
            PyObject *entry = build_entry(state, tree, child, uri);
            if (entry == NULL) { /* GCOVR_EXCL_BR_LINE: build_entry fails only on allocation */
                Py_DECREF(list); /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;     /* GCOVR_EXCL_LINE */
            }
            int append_failed = PyList_Append(list, entry) < 0;
            Py_DECREF(entry);
            if (append_failed) { /* GCOVR_EXCL_BR_LINE: append fails only on unforceable allocation */
                Py_DECREF(list); /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;     /* GCOVR_EXCL_LINE */
            }
        }
    }
    PyObject *entries = PyList_AsTuple(list);
    Py_DECREF(list);
    return entries;
}

/* Build the Feed(type, title, link, description, updated, entries) record rooted at `root`. RSS and RDF read their feed
   metadata from the <channel> element (absent in a malformed feed, leaving the fields None); Atom reads it from the
   feed root itself. RSS nests its <item>s in the channel, RDF makes them siblings of the channel, Atom nests its
   <entry>s in the root. NULL only on the excluded allocation-failure path. */
static PyObject *build_feed(module_state *state, th_tree *tree, th_node *root, const char *type) {
    static const feed_tag TITLE[] = {{"title", 5}};
    static const feed_tag DESCRIPTION[] = {{"description", 11}, {"subtitle", 8}, {"tagline", 7}};
    static const feed_tag UPDATED[] = {{"updated", 7}, {"lastbuilddate", 13}, {"pubdate", 7}, {"dc:date", 7}};
    int is_atom = strcmp(type, "atom") == 0;
    int rdf = strcmp(type, "rdf") == 0;
    const char *uri = is_atom ? ATOM_URI : rdf ? RSS_URI : NULL;
    int error = 0;
    th_node *meta = is_atom ? root : feed_child_named(tree, root, "channel", 7, uri, &error);
    if (error) {     /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
        return NULL; /* GCOVR_EXCL_LINE: allocation failure */
    }
    th_node *item_parent = is_atom || rdf ? root : meta;
    const char *item_name = is_atom ? "entry" : "item";
    Py_ssize_t item_len = is_atom ? 5 : 4;
    PyObject *record = PyTuple_New(6);
    if (record == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int failed = 0;
    failed |= record_set(record, 0, PyUnicode_FromString(type));
    failed |= record_set(record, 1, field_text(tree, meta, TITLE, 1, uri, is_atom));
    failed |= record_set(record, 2, extract_link(tree, meta, uri));
    failed |= record_set(record, 3, field_text(tree, meta, DESCRIPTION, 3, uri, is_atom));
    failed |= record_set(record, 4, field_text(tree, meta, UPDATED, 4, uri, 0));
    failed |= record_set(record, 5, build_entries(state, tree, item_parent, item_name, item_len, uri));
    if (failed != 0) {     /* GCOVR_EXCL_BR_LINE: a field build fails only on unforceable allocation */
        Py_DECREF(record); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    PyObject *feed = PyObject_Call(state->feed_type, record, NULL);
    Py_DECREF(record);
    return feed;
}

/* The feed root and its format: the first <feed> (atom), <rss> (rss), or <rdf:RDF> (rdf) element in document order, or
   NULL when the document is not a feed. */
static th_node *find_feed_root(th_tree *tree, th_node *document, const char **type_out, int *error) {
    for (th_node *node = document->first_child; node != NULL; node = preorder_next(node, document)) {
        if (node->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (tag_is(node, "feed", 4)) {
            *type_out = "atom";
            return node;
        }
        if (tag_is(node, "rss", 3)) {
            *type_out = "rss";
            return node;
        }
        int rdf = th_tree_is_xml(tree) ? qualified_tag_is(tree, node, "RDF", RDF_URI) : tag_is(node, "rdf:rdf", 7);
        if (rdf < 0) {   /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
            *error = 1;  /* GCOVR_EXCL_LINE: allocation failure */
            return NULL; /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (rdf) {
            *type_out = "rdf";
            return node;
        }
        int atom = th_tree_is_xml(tree) ? qualified_tag_is(tree, node, "feed", ATOM_URI) : 0;
        if (atom < 0) {  /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
            *error = 1;  /* GCOVR_EXCL_LINE: allocation failure */
            return NULL; /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (atom) {
            *type_out = "atom";
            return node;
        }
    }
    return NULL;
}

/* Document.feed() -> Feed | None. Normalizes an RSS 2.0, Atom 1.0, or RDF/RSS-1.0 document into one Feed record, or
   None when the document carries no feed root. */
TH_NODE_API(, PyObject *, turbohtml_document_feed, (PyObject * self, PyObject *ignored), (self, ignored),
            (PyObject * self, PyObject *Py_UNUSED(ignored)), (NodeObject *)self, NULL) {
    th_tree *tree = tree_of(self);
    module_state *state = state_of(self);
    th_node *document = ((NodeObject *)self)->node;
    PyObject *result = NULL;
    th_node *root = NULL;
    const char *type = NULL;
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    root = find_feed_root(tree, document, &type, &error);
    if (root != NULL) {
        result = build_feed(state, tree, root, type);
    }
    Py_END_CRITICAL_SECTION();
    if (error) {     /* GCOVR_EXCL_BR_LINE: namespace_is fails only on allocation */
        return NULL; /* GCOVR_EXCL_LINE: allocation failure */
    }
    if (root == NULL) {
        Py_RETURN_NONE;
    }
    return result; /* Feed on success, NULL with an error set only on the excluded allocation-failure path */
}

/* Store the Feed and Entry NamedTuple classes the walk builds its results from; turbohtml._feed registers both on
   import. */
PyObject *turbohtml_register_feed(PyObject *module, PyObject *args) {
    PyObject *feed = NULL;
    PyObject *entry = NULL;
    if (!PyArg_ParseTuple(args, "OO", &feed, &entry)) { /* GCOVR_EXCL_BR_LINE: the facade always registers with two */
        return NULL;                                    /* GCOVR_EXCL_LINE: argument-error path */
    }
    module_state *state = PyModule_GetState(module);
    Py_XSETREF(state->feed_type, Py_NewRef(feed));
    Py_XSETREF(state->entry_type, Py_NewRef(entry));
    Py_RETURN_NONE;
}
