/* Block-structure predicates and cell/number helpers the markdown and plain-text
   renderers share. Both walk the tree deciding what opens a line, what a run of
   prose is, and how a table row lays out; keeping these out of internal.h leaves
   that header to the HTML tag writers the other output modes need. */

#ifndef TURBOHTML_SERIALIZE_MARKDOWN_INTERNAL_H
#define TURBOHTML_SERIALIZE_MARKDOWN_INTERNAL_H

#include "serialize/buffer.h"

#include "core/ascii.h" /* is_space, the shared HTML ASCII-whitespace predicate */
#include "dom/tree.h"

/* A block-level element opens its own line(s); everything else is inline and
   flows into the surrounding line. Shared by the markdown and text renderers. */
static inline int is_md_block(uint16_t atom) {
    switch (atom) {
    case TH_TAG_HTML:
    case TH_TAG_BODY:
    case TH_TAG_P:
    case TH_TAG_DIV:
    case TH_TAG_SECTION:
    case TH_TAG_ARTICLE:
    case TH_TAG_HEADER:
    case TH_TAG_FOOTER:
    case TH_TAG_NAV:
    case TH_TAG_ASIDE:
    case TH_TAG_MAIN:
    case TH_TAG_FIGURE:
    case TH_TAG_FIGCAPTION:
    case TH_TAG_ADDRESS:
    case TH_TAG_BLOCKQUOTE:
    case TH_TAG_PRE:
    case TH_TAG_HR:
    case TH_TAG_H1:
    case TH_TAG_H2:
    case TH_TAG_H3:
    case TH_TAG_H4:
    case TH_TAG_H5:
    case TH_TAG_H6:
    case TH_TAG_UL:
    case TH_TAG_OL:
    case TH_TAG_LI:
    case TH_TAG_DL:
    case TH_TAG_DT:
    case TH_TAG_DD:
    case TH_TAG_MENU:
    case TH_TAG_DETAILS:
    case TH_TAG_SUMMARY:
    case TH_TAG_TABLE:
        return 1;
    default:
        return 0;
    }
}

/* Elements whose entire subtree contributes nothing to a text/markdown rendering:
   document metadata and scripts. <script>/<style> hold code, not prose, in every
   namespace (an inline-SVG stylesheet is CSS just like an HTML one), so they are
   dropped regardless of ns; <head> is an HTML-only concept. */
static inline int is_md_skipped(const th_node *node) {
    if (node->atom == TH_TAG_SCRIPT || node->atom == TH_TAG_STYLE) {
        return 1;
    }
    return node->ns == TH_NS_HTML && node->atom == TH_TAG_HEAD;
}

static inline Py_ssize_t md_put_decimal(sbuf *out, Py_ssize_t number) {
    Py_UCS4 digits[20];
    Py_ssize_t count = 0;
    do {
        digits[count++] = (Py_UCS4)('0' + (int)(number % 10));
        number /= 10;
    } while (number > 0);
    for (Py_ssize_t index = count - 1; index >= 0; index--) {
        sbuf_putc(out, digits[index]);
    }
    return count;
}

/* The value of one attribute by interned name, or NULL with *len 0 when the
   attribute is absent or valueless. */
static inline const Py_UCS4 *md_attr(th_tree *tree, th_node *node, const char *name, Py_ssize_t *len) {
    Py_ssize_t index = th_node_attr_find(tree, node, name, (Py_ssize_t)strlen(name));
    if (index < 0 || node->attrs[index].value == NULL) {
        *len = 0;
        return NULL;
    }
    *len = node->attrs[index].value_len;
    return node->attrs[index].value;
}

static inline Py_ssize_t md_row_cells(th_node *row) {
    Py_ssize_t count = 0;
    for (th_node *cell = row->first_child; cell != NULL; cell = cell->next_sibling) {
        if (cell->type == TH_NODE_ELEMENT && (cell->atom == TH_TAG_TD || cell->atom == TH_TAG_TH)) {
            count++;
        }
    }
    return count;
}

/* Trim the buffer in place under the document-strip mode, returning how many
   leading code points were removed so a caller can shift recorded offsets. */
static inline Py_ssize_t md_trim(sbuf *out, int mode) {
    Py_ssize_t start = 0;
    if (mode == TH_MD_DOC_STRIP || mode == TH_MD_DOC_LSTRIP) {
        /* strip leading blank lines only; block content never starts with a space
           except an indented code block, whose indent must survive */
        while (start < out->len && out->data[start] == '\n') {
            start++;
        }
    }
    Py_ssize_t end = out->len;
    if (mode == TH_MD_DOC_STRIP || mode == TH_MD_DOC_RSTRIP) {
        while (end > start && is_space(out->data[end - 1])) {
            end--;
        }
    }
    if (start > 0) {
        memmove(out->data, out->data + start, (size_t)(end - start) * sizeof(Py_UCS4));
    }
    out->len = end - start;
    return start; /* how many leading code points were removed, to shift annotations */
}

#endif /* TURBOHTML_SERIALIZE_MARKDOWN_INTERNAL_H */
