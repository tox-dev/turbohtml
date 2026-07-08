/* The lossless serializer to_source() drives: it re-emits the verbatim source
   bytes of every element start tag, end tag, and text run the parse left untouched,
   so a round trip over an unmodified tree reproduces the input -- author quoting,
   tag-name case, character-reference spelling, and insignificant whitespace intact.
   Only a node a mutation touched is reserialized: an element whose attributes
   changed rebuilds its start tag, an edited text run re-escapes, and an inserted
   element (which carries no source location) serializes canonically, while its
   untouched siblings still copy their original span. This is the tree-based
   counterpart to the streaming rewriter, built on the per-element spans a parse
   with source locations records. */

#include "serialize/internal.h"

#include "dom/tree.h"
#include "dom/tree_internal.h"

#include <string.h>

/* Append the source code points [start, end) to the buffer, widening the borrowed
   input to UCS-4. The three arms mirror the input's storage kind. */
static void sbuf_put_source(sbuf *out, th_tree *tree, Py_ssize_t start, Py_ssize_t end) {
    Py_ssize_t len = end - start;
    sbuf_reserve(out, len);
    if (out->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    Py_UCS4 *dst = out->data + out->len;
    if (tree->kind == PyUnicode_4BYTE_KIND) {
        memcpy(dst, (const Py_UCS4 *)tree->data + start, (size_t)len * sizeof(Py_UCS4));
    } else if (tree->kind == PyUnicode_1BYTE_KIND) {
        const uint8_t *bytes = (const uint8_t *)tree->data + start;
        for (Py_ssize_t index = 0; index < len; index++) {
            dst[index] = bytes[index];
        }
    } else {
        const uint16_t *units = (const uint16_t *)tree->data + start;
        for (Py_ssize_t index = 0; index < len; index++) {
            dst[index] = units[index];
        }
    }
    out->len += len;
}

/* The WHATWG-conformant escaping the reserialized parts fall back to. */
static const th_serialize_opts lossless_opts = {TH_FMT_WHATWG, 0, 0, NULL, 0, 0};

/* Emit a text node: its verbatim source span while it is still the zero-copy slice
   the parse left (character references and raw ampersands preserved), or the WHATWG
   escaping of its current code points once a read realized it or an edit replaced it. */
static void lossless_put_text(sbuf *out, th_tree *tree, th_node *node) {
    if (node->text == NULL && node->text_len > 0) {
        sbuf_put_source(out, tree, node->attr_count, node->attr_count + node->text_len);
    } else {
        sbuf_put_text(out, node->text, node->text_len, 0, TH_FMT_WHATWG);
    }
}

/* Emit a raw-text element's child: its verbatim source span while it is still the
   zero-copy slice the parse left (text NULL implies a positive-length span, since the
   builder never inserts an empty text node), else its current code points literally
   (raw-text content is never escaped, so both paths emit the bytes unchanged). */
static void lossless_put_rawtext(sbuf *out, th_tree *tree, th_node *node) {
    if (node->text == NULL) {
        sbuf_put_source(out, tree, node->attr_count, node->attr_count + node->text_len);
    } else {
        sbuf_put_ucs4(out, node->text, node->text_len);
    }
}

/* Write an element's start tag: its verbatim source bytes when the parse recorded a
   location the mutation API has not since dirtied, else a canonical rebuild from the
   current attributes. */
static void lossless_open_tag(sbuf *out, th_tree *tree, th_node *node, const th_src_loc *loc) {
    if (loc != NULL && !loc->start_dirty) {
        sbuf_put_source(out, tree, loc->start_tag.start_offset, loc->start_tag.end_offset);
    } else {
        ser_open_tag(out, tree, node, &lossless_opts);
        sbuf_putc(out, '>');
    }
}

/* Write an element's end tag: its verbatim source bytes when the source closed it,
   nothing when the source left it implicitly closed, and a canonical close tag for a
   synthetic or inserted element the parse never located. */
static void lossless_close_tag(sbuf *out, th_tree *tree, th_node *node, const th_src_loc *loc) {
    if (loc != NULL) {
        if (loc->has_end_tag) {
            sbuf_put_source(out, tree, loc->end_tag.start_offset, loc->end_tag.end_offset);
        }
    } else {
        ser_close_tag(out, node);
    }
}

/* Emit one node and return the next the walk rooted at root visits, or NULL once the
   subtree is done. Iterative -- descending through first_child, ascending through
   parent pointers -- so a tree of any depth serializes without one C stack frame per
   level, the same shape serialize_compact_step uses. */
static th_node *lossless_step(sbuf *out, th_tree *tree, th_node *node, th_node *root) {
    th_node *descend = NULL;
    switch (node->type) { /* GCOVR_EXCL_BR_LINE: th_node_type is exhaustive; the implicit default is unreachable */
    case TH_NODE_ELEMENT: {
        const th_src_loc *loc = th_node_source_location(tree, node);
        lossless_open_tag(out, tree, node, loc);
        if (node->ns == TH_NS_HTML && is_serialize_void_atom(node->atom)) {
            break; /* void elements have no children or end tag */
        }
        if (ser_needs_leading_newline(tree, node)) {
            sbuf_putc(out, '\n'); /* re-emit the newline pre/textarea/listing dropped on read */
        }
        if (is_rawtext_element(node, tree->scripting)) {
            for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
                lossless_put_rawtext(out, tree, child);
            }
            lossless_close_tag(out, tree, node, loc);
            break;
        }
        if (node->first_child != NULL) {
            descend = node->first_child;
        } else {
            lossless_close_tag(out, tree, node, loc);
        }
        break;
    }
    case TH_NODE_TEXT:
        lossless_put_text(out, tree, node);
        break;
    case TH_NODE_COMMENT:
        sbuf_puts(out, "<!--");
        sbuf_put_ucs4(out, node->text, node->text_len);
        sbuf_puts(out, "-->");
        break;
    case TH_NODE_DOCTYPE:
        sbuf_puts(out, "<!DOCTYPE ");
        sbuf_put_ucs4(out, node->text, doctype_name_len(node));
        sbuf_putc(out, '>');
        break;
    case TH_NODE_PI:
        sbuf_puts(out, "<?");
        sbuf_put_ucs4(out, node->text, node->text_len);
        sbuf_putc(out, '>');
        break;
    case TH_NODE_CDATA:
        sbuf_puts(out, "<![CDATA[");
        sbuf_put_ucs4(out, node->text, node->text_len);
        sbuf_puts(out, "]]>");
        break;
    case TH_NODE_CONTENT:
    case TH_NODE_DOCUMENT:
        descend = node->first_child; /* a transparent container emits only its children */
        break;
    }
    if (descend != NULL) {
        return descend;
    }
    /* ascend toward the root, closing each element whose children are done */
    while (node != root) {
        if (node->next_sibling != NULL) {
            return node->next_sibling;
        }
        node = node->parent;
        if (node->type == TH_NODE_ELEMENT) {
            lossless_close_tag(out, tree, node, th_node_source_location(tree, node));
        }
    }
    return NULL;
}

Py_UCS4 *th_node_serialize_source(th_tree *tree, th_node *node, Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0};
    sbuf_presize_for_root(&out, tree, node);
    th_node *cursor = node;
    while (cursor != NULL) {
        cursor = lossless_step(&out, tree, cursor, node);
    }
    return sbuf_finish(&out, out_len);
}
